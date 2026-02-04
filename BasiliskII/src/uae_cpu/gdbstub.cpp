#include "sysdeps.h"

#include "gdbstub.h"
#include "cpu_emulation.h"
#include "newcpu.h"

#include <errno.h>

#if defined(__unix__) || defined(__APPLE__)
#define B2_HAVE_GDBSTUB 1
#else
#define B2_HAVE_GDBSTUB 0
#endif

#if B2_HAVE_GDBSTUB
#include <arpa/inet.h>
#include <sys/socket.h>

#include <string>
#include <vector>

namespace {
static int gdb_listen_fd = -1;
static int gdb_client_fd = -1;
static bool gdb_enabled = false;
static bool gdb_wait_for_client = false;
static bool gdb_break_on_start = false;
static bool gdb_no_ack = false;
static bool gdb_single_step = false;
static bool gdb_in_stop = false;
static std::vector<uaecptr> gdb_breakpoints;        // Runtime addresses for immediate checking
static std::vector<uaecptr> gdb_breakpoint_vmas;    // Original VMAs for recalculation
static uaecptr gdb_code_base = 0;  // Base address where app CODE is loaded

// Segment tracking for multi-segment apps
// Simple array: index is segment number, value is runtime base address
// CODE segments are typically numbered 1-8, use 0 for segment 1
static uaecptr gdb_segment_bases[GDB_MAX_SEGMENTS];
static bool gdb_segment_loaded[GDB_MAX_SEGMENTS];

// Segment VMA info from .segments file (compile-time info)
struct SegmentVMAInfo {
	uaecptr vma_start;
	uaecptr vma_end;
	uaecptr ref_func_vma;  // VMA of reference function for this segment
	bool has_ref_func;
};
static SegmentVMAInfo gdb_segment_vma[GDB_MAX_SEGMENTS];
static bool gdb_segment_map_loaded = false;
static char gdb_segments_file_path[512] = "";

static const int kSigTrap = 5;
static const int kSigInt = 2;

static char hex_digit(unsigned int v)
{
	static const char *kHex = "0123456789abcdef";
	return kHex[v & 0x0f];
}

static int hex_value(char c)
{
	if (c >= '0' && c <= '9') return c - '0';
	if (c >= 'a' && c <= 'f') return c - 'a' + 10;
	if (c >= 'A' && c <= 'F') return c - 'A' + 10;
	return -1;
}

static void append_u8_hex(std::string &out, uint8 v)
{
	out.push_back(hex_digit(v >> 4));
	out.push_back(hex_digit(v));
}

static void append_u16_be(std::string &out, uint16 v)
{
	append_u8_hex(out, static_cast<uint8>((v >> 8) & 0xff));
	append_u8_hex(out, static_cast<uint8>(v & 0xff));
}

static void append_u32_be(std::string &out, uint32 v)
{
	append_u8_hex(out, static_cast<uint8>((v >> 24) & 0xff));
	append_u8_hex(out, static_cast<uint8>((v >> 16) & 0xff));
	append_u8_hex(out, static_cast<uint8>((v >> 8) & 0xff));
	append_u8_hex(out, static_cast<uint8>(v & 0xff));
}


static bool read_u32_be_from_hex(const std::string &in, size_t &idx, uint32 &v)
{
	if (idx + 8 > in.size()) return false;
	uint32 tmp = 0;
	for (int i = 0; i < 4; ++i) {
		int h = hex_value(in[idx++]);
		int l = hex_value(in[idx++]);
		if (h < 0 || l < 0) return false;
		tmp = (tmp << 8) | static_cast<uint32>((h << 4) | l);
	}
	v = tmp;
	return true;
}

static bool read_u16_be_from_hex(const std::string &in, size_t &idx, uint16 &v)
{
	if (idx + 4 > in.size()) return false;
	int h1 = hex_value(in[idx++]);
	int l1 = hex_value(in[idx++]);
	int h2 = hex_value(in[idx++]);
	int l2 = hex_value(in[idx++]);
	if (h1 < 0 || l1 < 0 || h2 < 0 || l2 < 0) return false;
	v = static_cast<uint16>((h1 << 12) | (l1 << 8) | (h2 << 4) | l2);
	return true;
}

static bool recv_byte(int fd, char &c)
{
	ssize_t n = recv(fd, &c, 1, 0);
	return n == 1;
}

static void send_raw(int fd, const std::string &data)
{
	const char *p = data.data();
	size_t left = data.size();
	while (left > 0) {
		ssize_t n = send(fd, p, left, 0);
		if (n <= 0) return;
		p += n;
		left -= static_cast<size_t>(n);
	}
}

static bool send_packet(const std::string &payload)
{
	if (gdb_client_fd < 0) return false;
	unsigned char csum = 0;
	for (size_t i = 0; i < payload.size(); ++i)
		csum += static_cast<unsigned char>(payload[i]);
	std::string pkt;
	pkt.reserve(payload.size() + 4);
	pkt.push_back('$');
	pkt += payload;
	pkt.push_back('#');
	pkt.push_back(hex_digit(csum >> 4));
	pkt.push_back(hex_digit(csum));
	send_raw(gdb_client_fd, pkt);
	if (gdb_no_ack) return true;
	char ack = 0;
	if (!recv_byte(gdb_client_fd, ack)) return false;
	return ack == '+';
}

static bool recv_packet(std::string &payload)
{
	payload.clear();
	if (gdb_client_fd < 0) return false;

	char c = 0;
	// Wait for '$'
	for (;;) {
		if (!recv_byte(gdb_client_fd, c)) return false;
		if (c == '$') break;
		if (c == '+') continue;
		if (c == '-') continue;
		if (c == 0x03) return false;
	}

	unsigned char csum = 0;
	for (;;) {
		if (!recv_byte(gdb_client_fd, c)) return false;
		if (c == '#') break;
		csum += static_cast<unsigned char>(c);
		payload.push_back(c);
	}
	char c1 = 0, c2 = 0;
	if (!recv_byte(gdb_client_fd, c1)) return false;
	if (!recv_byte(gdb_client_fd, c2)) return false;
	int h1 = hex_value(c1);
	int h2 = hex_value(c2);
	unsigned char rcv = (h1 < 0 || h2 < 0) ? 0 : static_cast<unsigned char>((h1 << 4) | h2);
	if (!gdb_no_ack) {
		char ack = (rcv == csum) ? '+' : '-';
		send_raw(gdb_client_fd, std::string(1, ack));
	}
	return rcv == csum;
}

static void close_client(void)
{
	if (gdb_client_fd >= 0) {
		close(gdb_client_fd);
		gdb_client_fd = -1;
	}
	gdb_no_ack = false;
	gdb_single_step = false;
	gdb_in_stop = false;
}

static bool try_accept(void)
{
	if (!gdb_enabled || gdb_client_fd >= 0 || gdb_listen_fd < 0) return false;

	struct sockaddr_in addr;
	socklen_t len = sizeof(addr);
	int fd = accept(gdb_listen_fd, reinterpret_cast<struct sockaddr *>(&addr), &len);
	if (fd < 0) return false;

	int flags = fcntl(fd, F_GETFL, 0);
	if (flags >= 0) fcntl(fd, F_SETFL, flags & ~O_NONBLOCK);
	gdb_client_fd = fd;
	return true;
}

static bool handle_qsupported(const std::string &payload)
{
	std::string resp = "PacketSize=4000;QStartNoAckMode+;qXfer:features:read+";
	return send_packet(resp);
}

static std::string target_xml(void)
{
	return
		"<?xml version=\"1.0\"?>"
		"<!DOCTYPE target SYSTEM \"gdb-target.dtd\">"
		"<target>"
		"<architecture>m68k</architecture>"
		"<feature name=\"org.gnu.gdb.m68k.core\">"
		"<reg name=\"d0\" bitsize=\"32\" regnum=\"0\"/>"
		"<reg name=\"d1\" bitsize=\"32\" regnum=\"1\"/>"
		"<reg name=\"d2\" bitsize=\"32\" regnum=\"2\"/>"
		"<reg name=\"d3\" bitsize=\"32\" regnum=\"3\"/>"
		"<reg name=\"d4\" bitsize=\"32\" regnum=\"4\"/>"
		"<reg name=\"d5\" bitsize=\"32\" regnum=\"5\"/>"
		"<reg name=\"d6\" bitsize=\"32\" regnum=\"6\"/>"
		"<reg name=\"d7\" bitsize=\"32\" regnum=\"7\"/>"
		"<reg name=\"a0\" bitsize=\"32\" regnum=\"8\"/>"
		"<reg name=\"a1\" bitsize=\"32\" regnum=\"9\"/>"
		"<reg name=\"a2\" bitsize=\"32\" regnum=\"10\"/>"
		"<reg name=\"a3\" bitsize=\"32\" regnum=\"11\"/>"
		"<reg name=\"a4\" bitsize=\"32\" regnum=\"12\"/>"
		"<reg name=\"a5\" bitsize=\"32\" regnum=\"13\"/>"
		"<reg name=\"a6\" bitsize=\"32\" regnum=\"14\"/>"
		"<reg name=\"a7\" bitsize=\"32\" regnum=\"15\"/>"
		"<reg name=\"pc\" bitsize=\"32\" regnum=\"16\" type=\"code_ptr\"/>"
		"<reg name=\"sr\" bitsize=\"32\" regnum=\"17\"/>"
		"</feature>"
		"</target>";
}

static bool handle_qxfer_features(const std::string &payload)
{
	const char *kPrefix = "qXfer:features:read:target.xml:";
	if (payload.find(kPrefix) != 0) return false;
	std::string view = payload.substr(strlen(kPrefix));
	size_t comma = view.find(',');
	if (comma == std::string::npos) return send_packet("E01");
	unsigned long offset = strtoul(view.substr(0, comma).c_str(), NULL, 16);
	unsigned long length = strtoul(view.substr(comma + 1).c_str(), NULL, 16);
	std::string xml = target_xml();
	if (offset >= xml.size()) return send_packet("l");
	unsigned long remaining = static_cast<unsigned long>(xml.size() - offset);
	unsigned long chunk = (length < remaining) ? length : remaining;
	char marker = (chunk == remaining) ? 'l' : 'm';
	std::string data(1, marker);
	data.append(xml.substr(offset, chunk));
	return send_packet(data);
}

static void read_registers(std::string &out)
{
	out.clear();
	MakeSR();
	for (int i = 0; i < 8; ++i) append_u32_be(out, m68k_dreg(regs, i));
	for (int i = 0; i < 8; ++i) append_u32_be(out, m68k_areg(regs, i));
	append_u32_be(out, m68k_getpc());
	append_u32_be(out, static_cast<uint32>(regs.sr));
}

static bool write_registers(const std::string &in)
{
	size_t idx = 0;
	for (int i = 0; i < 8; ++i) {
		uint32 v = 0;
		if (!read_u32_be_from_hex(in, idx, v)) return false;
		m68k_dreg(regs, i) = v;
	}
	for (int i = 0; i < 8; ++i) {
		uint32 v = 0;
		if (!read_u32_be_from_hex(in, idx, v)) return false;
		m68k_areg(regs, i) = v;
	}
	uint32 pc = 0;
	if (!read_u32_be_from_hex(in, idx, pc)) return false;
	m68k_setpc(pc);
	uint32 sr32 = 0;
	if (!read_u32_be_from_hex(in, idx, sr32)) return false;
	regs.sr = static_cast<uint16>(sr32 & 0xffff);
	MakeFromSR();
	return true;
}

static bool read_register_by_num(int regno, std::string &out)
{
	out.clear();
	MakeSR();
	if (regno >= 0 && regno <= 7) {
		append_u32_be(out, m68k_dreg(regs, regno));
		return true;
	}
	if (regno >= 8 && regno <= 15) {
		append_u32_be(out, m68k_areg(regs, regno - 8));
		return true;
	}
	if (regno == 16) {
		append_u32_be(out, m68k_getpc());
		return true;
	}
	if (regno == 17) {
		append_u32_be(out, static_cast<uint32>(regs.sr));
		return true;
	}
	return false;
}

static bool write_register_by_num(int regno, const std::string &hex)
{
	if (regno >= 0 && regno <= 7) {
		if (hex.size() != 8) return false;
		uint32 v = 0;
		for (int i = 0; i < 4; ++i) {
			int h = hex_value(hex[i * 2]);
			int l = hex_value(hex[i * 2 + 1]);
			if (h < 0 || l < 0) return false;
			v = (v << 8) | static_cast<uint32>((h << 4) | l);
		}
		m68k_dreg(regs, regno) = v;
		return true;
	}
	if (regno >= 8 && regno <= 15) {
		if (hex.size() != 8) return false;
		uint32 v = 0;
		for (int i = 0; i < 4; ++i) {
			int h = hex_value(hex[i * 2]);
			int l = hex_value(hex[i * 2 + 1]);
			if (h < 0 || l < 0) return false;
			v = (v << 8) | static_cast<uint32>((h << 4) | l);
		}
		m68k_areg(regs, regno - 8) = v;
		return true;
	}
	if (regno == 16) {
		if (hex.size() != 8) return false;
		uint32 v = 0;
		for (int i = 0; i < 4; ++i) {
			int h = hex_value(hex[i * 2]);
			int l = hex_value(hex[i * 2 + 1]);
			if (h < 0 || l < 0) return false;
			v = (v << 8) | static_cast<uint32>((h << 4) | l);
		}
		m68k_setpc(v);
		return true;
	}
	if (regno == 17) {
		if (hex.size() != 8) return false;
		uint32 v = 0;
		for (int i = 0; i < 4; ++i) {
			int h = hex_value(hex[i * 2]);
			int l = hex_value(hex[i * 2 + 1]);
			if (h < 0 || l < 0) return false;
			v = (v << 8) | static_cast<uint32>((h << 4) | l);
		}
		regs.sr = static_cast<uint16>(v & 0xffff);
		MakeFromSR();
		return true;
	}
	return false;
}

static bool handle_read_memory(const std::string &payload)
{
	size_t comma = payload.find(',');
	if (comma == std::string::npos) return send_packet("E01");
	uaecptr addr = static_cast<uaecptr>(strtoul(payload.substr(1, comma - 1).c_str(), NULL, 16));
	uint32 len = static_cast<uint32>(strtoul(payload.substr(comma + 1).c_str(), NULL, 16));
	std::string out;
	out.reserve(len * 2);
	for (uint32 i = 0; i < len; ++i) {
		uint8 b = static_cast<uint8>(ReadMacInt8(addr + i));
		append_u8_hex(out, b);
	}
	return send_packet(out);
}

static bool handle_write_memory(const std::string &payload)
{
	size_t comma = payload.find(',');
	size_t colon = payload.find(':');
	if (comma == std::string::npos || colon == std::string::npos || colon < comma) return send_packet("E01");
	uaecptr addr = static_cast<uaecptr>(strtoul(payload.substr(1, comma - 1).c_str(), NULL, 16));
	uint32 len = static_cast<uint32>(strtoul(payload.substr(comma + 1, colon - comma - 1).c_str(), NULL, 16));
	const std::string data = payload.substr(colon + 1);
	if (data.size() < len * 2) return send_packet("E02");
	for (uint32 i = 0; i < len; ++i) {
		int h = hex_value(data[i * 2]);
		int l = hex_value(data[i * 2 + 1]);
		if (h < 0 || l < 0) return send_packet("E03");
		uint8 b = static_cast<uint8>((h << 4) | l);
		WriteMacInt8(addr + i, b);
	}
	return send_packet("OK");
}

// Load segment VMA info from .segments file
static void load_segments_file(void)
{
	if (gdb_segment_map_loaded || gdb_segments_file_path[0] == '\0') return;

	FILE *f = fopen(gdb_segments_file_path, "r");
	if (!f) {
		fprintf(stderr, "GDB stub: cannot open segments file %s\n", gdb_segments_file_path);
		fflush(stderr);
		return;
	}

	char line[256];
	while (fgets(line, sizeof(line), f)) {
		int seg_num;
		unsigned int vma_start, vma_end, ref_vma;

		if (sscanf(line, "SEGMENT %d 0x%x 0x%x", &seg_num, &vma_start, &vma_end) == 3) {
			if (seg_num > 0 && seg_num < GDB_MAX_SEGMENTS) {
				gdb_segment_vma[seg_num].vma_start = vma_start;
				gdb_segment_vma[seg_num].vma_end = vma_end;
			}
		} else if (sscanf(line, "REF_FUNC %d 0x%x", &seg_num, &ref_vma) == 2) {
			if (seg_num > 0 && seg_num < GDB_MAX_SEGMENTS) {
				gdb_segment_vma[seg_num].ref_func_vma = ref_vma;
				gdb_segment_vma[seg_num].has_ref_func = true;
			}
		}
	}
	fclose(f);
	gdb_segment_map_loaded = true;

	fprintf(stderr, "GDB stub: loaded segment map:\n");
	for (int i = 1; i < GDB_MAX_SEGMENTS; i++) {
		if (gdb_segment_vma[i].vma_end > 0) {
			fprintf(stderr, "  seg %d: VMA 0x%08x-0x%08x", i,
				gdb_segment_vma[i].vma_start, gdb_segment_vma[i].vma_end);
			if (gdb_segment_vma[i].has_ref_func) {
				fprintf(stderr, " ref=0x%08x", gdb_segment_vma[i].ref_func_vma);
			}
			fprintf(stderr, "\n");
		}
	}
	fflush(stderr);
}

// Find which segment a VMA belongs to
static int find_segment_for_vma(uaecptr vma)
{
	for (int i = 1; i < GDB_MAX_SEGMENTS; i++) {
		if (vma >= gdb_segment_vma[i].vma_start && vma < gdb_segment_vma[i].vma_end) {
			return i;
		}
	}
	return 0;
}

// Calculate segment base from JT entry address and reference function VMA
static uaecptr calculate_segment_base(int seg_num, uaecptr jt_func_runtime)
{
	if (seg_num <= 0 || seg_num >= GDB_MAX_SEGMENTS) return 0;
	if (!gdb_segment_vma[seg_num].has_ref_func) return 0;

	uaecptr ref_vma = gdb_segment_vma[seg_num].ref_func_vma;
	uaecptr seg_vma_start = gdb_segment_vma[seg_num].vma_start;
	uaecptr ref_offset = ref_vma - seg_vma_start;

	return jt_func_runtime - ref_offset;
}

// Find segment 2 base by scanning for relocated JSR instructions in segment 1
// Retro68 patches direct JSR instructions with runtime addresses after relocation
// We look for JSR.L instructions whose targets point to addresses that could be segment 2
static void find_segment2_from_jsr(void)
{
	if (!gdb_segment_map_loaded) return;
	if (!gdb_segment_loaded[1]) return;
	if (gdb_segment_loaded[2]) return;  // Already found
	if (!gdb_segment_vma[2].has_ref_func) return;  // Need ref func to calculate base

	uaecptr seg1_base = gdb_segment_bases[1];
	uaecptr seg1_size = gdb_segment_vma[1].vma_end - gdb_segment_vma[1].vma_start;
	uaecptr seg2_ref_vma = gdb_segment_vma[2].ref_func_vma;
	uaecptr seg2_vma_start = gdb_segment_vma[2].vma_start;
	uaecptr seg2_ref_offset = seg2_ref_vma - seg2_vma_start;

	fprintf(stderr, "GDB: Scanning segment 1 for JSR.L to segment 2 ref_func\n");
	fprintf(stderr, "     seg1_base=0x%08x, seg1_size=0x%x\n", seg1_base, seg1_size);
	fprintf(stderr, "     seg2_ref_vma=0x%08x, seg2_ref_offset=0x%x\n", seg2_ref_vma, seg2_ref_offset);
	fflush(stderr);

	// Scan segment 1 for JSR.L (0x4EB9) instructions
	// Don't scan the whole segment - just the first part where startup code lives
	uaecptr scan_limit = seg1_size > 0x1000 ? 0x1000 : seg1_size;
	for (uaecptr offset = 0; offset < scan_limit; offset += 2) {
		uae_u16 opcode = get_word(seg1_base + offset);
		if (opcode == 0x4EB9) {
			// Found JSR.L - read the target address
			uaecptr target = get_long(seg1_base + offset + 2);

			// Check if this could be the reference function for segment 2
			// The target should be above segment 1 (different memory block)
			// and when we calculate segment 2 base from it, the base should be reasonable
			if (target > seg1_base + seg1_size) {
				// Calculate what seg2 base would be if this JSR targets the ref func
				uaecptr possible_seg2_base = target - seg2_ref_offset;

				// Verify by checking if the target function looks like code
				// LINK instruction (4E56) is common at function start
				uae_u16 target_opcode = get_word(target);
				if (target_opcode == 0x4E56 || target_opcode == 0x4E75 ||
				    (target_opcode & 0xF000) == 0x4000) {  // 4xxx family opcodes
					fprintf(stderr, "GDB: Found JSR.L at offset 0x%x -> target 0x%08x (opcode 0x%04x)\n",
						offset, target, target_opcode);
					fprintf(stderr, "     Calculated seg2_base = 0x%08x\n", possible_seg2_base);
					fflush(stderr);
					GdbStubAddSegment(2, possible_seg2_base);
					return;
				}
			}
		}
	}
	fprintf(stderr, "GDB: No suitable JSR.L found in segment 1 scan\n");
	fflush(stderr);
}

// Translate a symbol VMA to runtime address
static uaecptr translate_symbol_to_runtime(uaecptr symbol_vma)
{
	load_segments_file();

	int seg = find_segment_for_vma(symbol_vma);
	if (seg == 0) {
		// Not in any known segment, use old behavior
		return symbol_vma + gdb_code_base;
	}

	// Check if we have a runtime base for this segment
	if (gdb_segment_loaded[seg]) {
		// Calculate offset within segment
		uaecptr offset = symbol_vma - gdb_segment_vma[seg].vma_start;
		uaecptr runtime = gdb_segment_bases[seg] + offset;
		fprintf(stderr, "GDB: symbol 0x%08x -> seg %d offset 0x%x -> runtime 0x%08x\n",
			symbol_vma, seg, offset, runtime);
		fflush(stderr);
		return runtime;
	}

	// Segment not loaded yet - return 0 to indicate translation not possible
	// The breakpoint will be recalculated when the segment loads
	fprintf(stderr, "GDB: segment %d not loaded, breakpoint deferred\n", seg);
	fflush(stderr);
	return 0;  // Invalid address signals translation not yet possible
}

// Recalculate all breakpoint runtime addresses from stored VMAs
static void recalculate_breakpoints(void)
{
	if (gdb_breakpoint_vmas.empty()) return;

	fprintf(stderr, "Recalculating %zu breakpoints after segment load\n", gdb_breakpoint_vmas.size());
	gdb_breakpoints.clear();

	for (size_t i = 0; i < gdb_breakpoint_vmas.size(); ++i) {
		uaecptr vma = gdb_breakpoint_vmas[i];
		uaecptr runtime = translate_symbol_to_runtime(vma);
		gdb_breakpoints.push_back(runtime);
		fprintf(stderr, "  Breakpoint %zu: VMA 0x%08x -> runtime 0x%08x\n", i, vma, runtime);
	}
	fflush(stderr);
}

static bool handle_breakpoint(const std::string &payload, bool set)
{
	if (payload.size() < 4) return send_packet("E01");
	if (payload[1] != '0') return send_packet("");
	size_t comma1 = payload.find(',');
	if (comma1 == std::string::npos) return send_packet("E01");
	size_t comma2 = payload.find(',', comma1 + 1);
	uaecptr addr = static_cast<uaecptr>(strtoul(payload.substr(comma1 + 1, comma2 - comma1 - 1).c_str(), NULL, 16));

	// Apply address translation for symbol addresses (< 1MB)
	// High addresses (> 1MB) are already runtime addresses, don't translate
	uaecptr runtime_addr;
	uaecptr vma_addr = 0;  // Store VMA for recalculation
	if (addr < 0x100000) {
		// Low address - assume it's a symbol VMA, translate using segment info
		vma_addr = addr;
		runtime_addr = translate_symbol_to_runtime(addr);
		fprintf(stderr, "GDB breakpoint %s: symbol=0x%08x -> runtime=0x%08x\n",
			set ? "SET" : "REMOVE", addr, runtime_addr);
	} else {
		// High address - already a runtime address, use as-is
		runtime_addr = addr;
		fprintf(stderr, "GDB breakpoint %s: raw runtime=0x%08x (no offset applied)\n",
			set ? "SET" : "REMOVE", runtime_addr);
	}
	fflush(stderr);

	if (set) {
		// Check if already exists
		for (size_t i = 0; i < gdb_breakpoints.size(); ++i) {
			if (gdb_breakpoints[i] == runtime_addr && runtime_addr != 0) return send_packet("OK");
		}
		// Only add to active breakpoints if we got a valid runtime address
		if (runtime_addr != 0) {
			gdb_breakpoints.push_back(runtime_addr);
		}
		// Always store the VMA for recalculation when segments load
		if (vma_addr != 0) {
			// Check if VMA already stored
			bool found = false;
			for (size_t i = 0; i < gdb_breakpoint_vmas.size(); ++i) {
				if (gdb_breakpoint_vmas[i] == vma_addr) { found = true; break; }
			}
			if (!found) {
				gdb_breakpoint_vmas.push_back(vma_addr);
			}
		}
		fprintf(stderr, "GDB breakpoints: %zu active, %zu pending\n",
			gdb_breakpoints.size(), gdb_breakpoint_vmas.size());
		fflush(stderr);
		return send_packet("OK");
	}
	// Don't actually remove breakpoints - we want them to persist after GDB disconnects
	// so we can reconnect when they hit. Just acknowledge the request.
	fprintf(stderr, "GDB breakpoint remove IGNORED (persisting for reconnect)\n");
	fflush(stderr);
	return send_packet("OK");
}

static bool handle_command(const std::string &payload)
{
	if (payload.empty()) return send_packet("");

	if (payload[0] == '?') {
		return send_packet("S05");
	}
	if (payload == "qSupported" || payload.find("qSupported:") == 0) {
		return handle_qsupported(payload);
	}
	if (payload.find("qXfer:features:read:") == 0) {
		return handle_qxfer_features(payload);
	}
	if (payload == "QStartNoAckMode") {
		// Switch to no-ack mode before replying so we don't block waiting for an ack.
		gdb_no_ack = true;
		return send_packet("OK");
	}
	if (payload == "vMustReplyEmpty") {
		// Per protocol, reply with an empty packet.
		return send_packet("");
	}
	if (payload == "qAttached") {
		return send_packet("1");
	}
	if (payload == "qOffsets") {
		return send_packet("Text=0;Data=0;Bss=0");
	}
	if (payload == "qC") {
		return send_packet("QC0");
	}
	if (payload == "qfThreadInfo" || payload == "qsThreadInfo") {
		return send_packet("l");
	}
	if (payload == "Hc-1" || payload == "Hg-1" || payload == "Hc0" || payload == "Hg0") {
		return send_packet("OK");
	}
	if (payload == "vCont?") {
		return send_packet("vCont;c;s");
	}
	if (payload[0] == 'g') {
		std::string regs_hex;
		read_registers(regs_hex);
		return send_packet(regs_hex);
	}
	if (payload[0] == 'G') {
		if (!write_registers(payload.substr(1))) return send_packet("E01");
		return send_packet("OK");
	}
	if (payload[0] == 'p') {
		int regno = static_cast<int>(strtoul(payload.substr(1).c_str(), NULL, 16));
		std::string reg_hex;
		if (!read_register_by_num(regno, reg_hex)) return send_packet("E01");
		return send_packet(reg_hex);
	}
	if (payload[0] == 'P') {
		size_t eq = payload.find('=');
		if (eq == std::string::npos) return send_packet("E01");
		int regno = static_cast<int>(strtoul(payload.substr(1, eq - 1).c_str(), NULL, 16));
		std::string hex = payload.substr(eq + 1);
		if (!write_register_by_num(regno, hex)) return send_packet("E01");
		return send_packet("OK");
	}
	if (payload[0] == 'm') {
		return handle_read_memory(payload);
	}
	if (payload[0] == 'M') {
		return handle_write_memory(payload);
	}
	if (payload[0] == 'Z') {
		return handle_breakpoint(payload, true);
	}
	if (payload[0] == 'z') {
		return handle_breakpoint(payload, false);
	}
	if (payload[0] == 'c') {
		gdb_single_step = false;
		return true;
	}
	if (payload[0] == 's') {
		gdb_single_step = true;
		return true;
	}
	if (payload[0] == 'D') {
		send_packet("OK");
		close_client();
		return true;
	}
	if (payload[0] == 'k') {
		send_packet("OK");
		close_client();
		return true;
	}

	return send_packet("");
}

static void handle_stop_loop(int signal)
{
	if (gdb_client_fd < 0) return;
	if (gdb_in_stop) return;
	gdb_in_stop = true;

	std::string stop_reply = "S";
	append_u8_hex(stop_reply, static_cast<uint8>(signal));
	send_packet(stop_reply);

	for (;;) {
		std::string payload;
		if (!recv_packet(payload)) {
			close_client();
			break;
		}
		if (payload.empty()) continue;
		char cmd = payload[0];
		if (cmd == 'c') {
			gdb_single_step = false;
			break;
		}
		if (cmd == 's') {
			gdb_single_step = true;
			break;
		}
		if (cmd == 0x03) {
			continue;
		}
		if (!handle_command(payload)) {
			close_client();
			break;
		}
	}
	gdb_in_stop = false;
}

} // namespace

#endif // B2_HAVE_GDBSTUB

bool GdbStubInit(uint16 port, bool wait_for_client, bool break_on_start)
{
#if B2_HAVE_GDBSTUB
	if (port == 0) {
		gdb_enabled = false;
		return false;
	}

	gdb_wait_for_client = wait_for_client;
	gdb_break_on_start = break_on_start;
	gdb_enabled = true;

	gdb_listen_fd = socket(AF_INET, SOCK_STREAM, 0);
	if (gdb_listen_fd < 0) {
		gdb_enabled = false;
		return false;
	}

	int on = 1;
	setsockopt(gdb_listen_fd, SOL_SOCKET, SO_REUSEADDR, &on, sizeof(on));

	struct sockaddr_in addr;
	memset(&addr, 0, sizeof(addr));
	addr.sin_family = AF_INET;
	addr.sin_port = htons(port);
	addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
	if (bind(gdb_listen_fd, reinterpret_cast<struct sockaddr *>(&addr), sizeof(addr)) < 0) {
		close(gdb_listen_fd);
		gdb_listen_fd = -1;
		gdb_enabled = false;
		return false;
	}
	if (listen(gdb_listen_fd, 1) < 0) {
		close(gdb_listen_fd);
		gdb_listen_fd = -1;
		gdb_enabled = false;
		return false;
	}

	int flags = fcntl(gdb_listen_fd, F_GETFL, 0);
	if (flags >= 0) fcntl(gdb_listen_fd, F_SETFL, flags | O_NONBLOCK);
	return true;
#else
	(void)port;
	(void)wait_for_client;
	(void)break_on_start;
	return false;
#endif
}

void GdbStubShutdown(void)
{
#if B2_HAVE_GDBSTUB
	close_client();
	if (gdb_listen_fd >= 0) {
		close(gdb_listen_fd);
		gdb_listen_fd = -1;
	}
	gdb_enabled = false;
	gdb_wait_for_client = false;
	gdb_break_on_start = false;
	gdb_breakpoints.clear();
#endif
}

bool GdbStubIsEnabled(void)
{
#if B2_HAVE_GDBSTUB
	return gdb_enabled;
#else
	return false;
#endif
}

bool GdbStubIsConnected(void)
{
#if B2_HAVE_GDBSTUB
	return gdb_client_fd >= 0;
#else
	return false;
#endif
}

void GdbStubSetCodeBase(uaecptr base)
{
#if B2_HAVE_GDBSTUB
	gdb_code_base = base;
	fprintf(stderr, "GDB stub: CODE base address set to 0x%08x\n", base);
	fflush(stderr);
#else
	(void)base;
#endif
}

uaecptr GdbStubGetCodeBase(void)
{
#if B2_HAVE_GDBSTUB
	return gdb_code_base;
#else
	return 0;
#endif
}

void GdbStubAddSegment(uint16 seg_num, uaecptr runtime_addr)
{
#if B2_HAVE_GDBSTUB
	if (seg_num < GDB_MAX_SEGMENTS) {
		bool was_loaded = gdb_segment_loaded[seg_num];
		gdb_segment_bases[seg_num] = runtime_addr;
		gdb_segment_loaded[seg_num] = true;
		fprintf(stderr, "GDB stub: CODE segment %d loaded at 0x%08x\n", seg_num, runtime_addr);
		fflush(stderr);

		// Recalculate breakpoints when a new segment loads
		if (!was_loaded && !gdb_breakpoint_vmas.empty()) {
			recalculate_breakpoints();
		}
	}
#else
	(void)seg_num;
	(void)runtime_addr;
#endif
}

void GdbStubSetSegmentsFile(const char *path)
{
#if B2_HAVE_GDBSTUB
	strncpy(gdb_segments_file_path, path, sizeof(gdb_segments_file_path) - 1);
	gdb_segments_file_path[sizeof(gdb_segments_file_path) - 1] = '\0';
	fprintf(stderr, "GDB stub: segments file set to %s\n", gdb_segments_file_path);
	fflush(stderr);
#else
	(void)path;
#endif
}

// Stored A5 value for JT rescanning
static uaecptr gdb_a5_value = 0;

void GdbStubSetA5(uaecptr a5)
{
#if B2_HAVE_GDBSTUB
	gdb_a5_value = a5;
#else
	(void)a5;
#endif
}

void GdbStubRescanJumpTable(void)
{
#if B2_HAVE_GDBSTUB
	if (gdb_a5_value == 0) return;

	// Load segment VMA info from .segments file
	load_segments_file();

	fprintf(stderr, "Rescanning jump table at A5=0x%08x\n", gdb_a5_value);
	fflush(stderr);

	// Calculate segment 1 base from the initial PC (from debugger trap)
	// The trap fires at the start of the app, so PC = runtime address of entry point
	// gdb_code_base holds this PC value
	if (gdb_segment_map_loaded && gdb_code_base != 0 && !gdb_segment_loaded[1]) {
		if (gdb_segment_vma[1].has_ref_func) {
			// Calculate segment 1 base: PC - (ref_func_vma - seg_vma_start)
			uaecptr seg1_base = calculate_segment_base(1, gdb_code_base);
			fprintf(stderr, "Segment 1 base from initial PC: 0x%08x -> base 0x%08x\n",
				gdb_code_base, seg1_base);
			fprintf(stderr, "  (ref_func_vma=0x%08x, seg_vma_start=0x%08x, offset=0x%x)\n",
				gdb_segment_vma[1].ref_func_vma,
				gdb_segment_vma[1].vma_start,
				gdb_segment_vma[1].ref_func_vma - gdb_segment_vma[1].vma_start);
			fflush(stderr);
			GdbStubAddSegment(1, seg1_base);
		} else {
			// No reference function, assume gdb_code_base is the segment start
			fprintf(stderr, "Segment 1 base (no ref func): 0x%08x\n", gdb_code_base);
			fflush(stderr);
			GdbStubAddSegment(1, gdb_code_base);
		}
	}

	// For Retro68 apps: find segment 2 by reading relocated JSR target
	// This works because Retro68 patches JSR addresses at load time
	find_segment2_from_jsr();

	// Track which segments we've already processed (use first JT entry found)
	bool segment_processed[GDB_MAX_SEGMENTS] = {false};

	// Scan JT entries looking for newly loaded segments (segments 2+)
	// JT can be large - scan up to 0x800 (256 entries)
	int entries_found = 0;
	for (int offset = 0x20; offset < 0x800; offset += 8) {
		uaecptr entry = gdb_a5_value + offset;
		uae_u16 seg_num = get_word(entry);
		uae_u16 opcode = get_word(entry + 2);
		uaecptr jt_func_addr = get_long(entry + 4);

		// Skip invalid entries
		if (seg_num == 0 && opcode == 0) continue;
		if (seg_num == 0 || seg_num > 15) continue;

		entries_found++;
		// Log all valid entries for debugging
		if (opcode == 0x4EF9) {
			fprintf(stderr, "  JT[0x%03x]: seg %d LOADED addr=0x%08x\n", offset, seg_num, jt_func_addr);
		} else if (opcode == 0xA9F0) {
			fprintf(stderr, "  JT[0x%03x]: seg %d UNLOADED offset=0x%08x\n", offset, seg_num, jt_func_addr);
		} else {
			fprintf(stderr, "  JT[0x%03x]: seg %d opcode=0x%04x addr=0x%08x\n", offset, seg_num, opcode, jt_func_addr);
		}

		if (opcode == 0x4EF9) {  // JMP - loaded segment
			// Only process first JT entry for each segment (assume it's the ref func)
			if (segment_processed[seg_num]) continue;
			segment_processed[seg_num] = true;

			// Calculate actual segment base using reference function VMA
			uaecptr seg_base;
			if (gdb_segment_map_loaded && gdb_segment_vma[seg_num].has_ref_func) {
				// Use reference function to calculate proper segment base
				seg_base = calculate_segment_base(seg_num, jt_func_addr);
				fprintf(stderr, "JT rescan: seg %d JT func at 0x%08x -> base 0x%08x\n",
					seg_num, jt_func_addr, seg_base);
				fprintf(stderr, "  (ref_func_vma=0x%08x, seg_vma_start=0x%08x, offset=0x%x)\n",
					gdb_segment_vma[seg_num].ref_func_vma,
					gdb_segment_vma[seg_num].vma_start,
					gdb_segment_vma[seg_num].ref_func_vma - gdb_segment_vma[seg_num].vma_start);
			} else {
				// No segment map, use JT address directly (old behavior)
				seg_base = jt_func_addr;
				fprintf(stderr, "JT rescan: seg %d at 0x%08x (no segment map)\n",
					seg_num, jt_func_addr);
			}
			fflush(stderr);

			// Update segment base if changed
			if (!gdb_segment_loaded[seg_num] || gdb_segment_bases[seg_num] != seg_base) {
				GdbStubAddSegment(seg_num, seg_base);
			}
		}
	}
	fprintf(stderr, "JT rescan complete: %d entries found\n", entries_found);
	fflush(stderr);
#endif
}

void GdbStubOnCpuStart(void)
{
#if B2_HAVE_GDBSTUB
	if (!gdb_enabled) return;
	if (gdb_wait_for_client) {
		while (!try_accept()) {
			usleep(10000);
		}
	}
	if (gdb_break_on_start && gdb_client_fd >= 0) {
		handle_stop_loop(kSigTrap);
	}
#endif
}

static bool gdb_new_connection = false;

void GdbStubMaybeAccept(void)
{
#if B2_HAVE_GDBSTUB
	if (!gdb_enabled) return;
	if (try_accept()) {
		// New client connected - set flag to trigger stop
		gdb_new_connection = true;
	}
#endif
}

bool GdbStubNewConnection(void)
{
#if B2_HAVE_GDBSTUB
	if (gdb_new_connection) {
		gdb_new_connection = false;
		return true;
	}
#endif
	return false;
}

bool GdbStubCheckAsyncBreak(void)
{
#if B2_HAVE_GDBSTUB
	if (!gdb_enabled || gdb_client_fd < 0) return false;
	fd_set rfds;
	FD_ZERO(&rfds);
	FD_SET(gdb_client_fd, &rfds);
	struct timeval tv;
	tv.tv_sec = 0;
	tv.tv_usec = 0;
	int ret = select(gdb_client_fd + 1, &rfds, NULL, NULL, &tv);
	if (ret <= 0) return false;
	char buf[64];
	ssize_t n = recv(gdb_client_fd, buf, sizeof(buf), MSG_DONTWAIT);
	if (n <= 0) return false;
	for (ssize_t i = 0; i < n; ++i) {
		if (buf[i] == 0x03) return true;
	}
	return false;
#else
	return false;
#endif
}

static uaecptr last_logged_pc = 0;
static int pc_log_counter = 0;

bool GdbStubCheckBreakpoint(uaecptr pc)
{
#if B2_HAVE_GDBSTUB
	// Check breakpoints even if GDB is not connected - we'll wait for reconnect
	if (!gdb_enabled) return false;

	// Log PC range periodically to help debug address issues
	if (gdb_breakpoints.size() > 0 && ++pc_log_counter >= 1000000) {
		pc_log_counter = 0;
		if (pc != last_logged_pc) {
			fprintf(stderr, "PC sample: 0x%08x (breakpoint at 0x%08x)\n", pc, gdb_breakpoints[0]);
			fflush(stderr);
			last_logged_pc = pc;
		}
	}

	for (size_t i = 0; i < gdb_breakpoints.size(); ++i) {
		if (gdb_breakpoints[i] == pc) {
			fprintf(stderr, "*** BREAKPOINT HIT at PC=0x%08x ***\n", pc);
			fflush(stderr);
			return true;
		}
	}
	return false;
#else
	(void)pc;
	return false;
#endif
}

bool GdbStubConsumeSingleStep(void)
{
#if B2_HAVE_GDBSTUB
	if (!gdb_enabled || gdb_client_fd < 0) return false;
	if (!gdb_single_step) return false;
	gdb_single_step = false;
	return true;
#else
	return false;
#endif
}

void GdbStubEnterStop(int signal)
{
#if B2_HAVE_GDBSTUB
	if (!gdb_enabled) {
		fprintf(stderr, "GdbStubEnterStop: gdb not enabled\n");
		fflush(stderr);
		return;
	}
	fprintf(stderr, "GdbStubEnterStop: signal=%d, client_fd=%d\n", signal, gdb_client_fd);
	fflush(stderr);
	if (gdb_client_fd < 0) {
		// Always wait for GDB to connect when hitting a breakpoint
		fprintf(stderr, "*** Breakpoint hit, waiting for GDB to connect... ***\n");
		fflush(stderr);
		while (!try_accept()) {
			usleep(10000);
		}
		fprintf(stderr, "*** GDB connected ***\n");
		fflush(stderr);
		// Rescan JT to find segments that were loaded while running
		GdbStubRescanJumpTable();
	}
	if (signal == 0) signal = kSigTrap;
	if (signal != kSigTrap && signal != kSigInt) signal = kSigTrap;
	handle_stop_loop(signal);
#else
	(void)signal;
#endif
}
