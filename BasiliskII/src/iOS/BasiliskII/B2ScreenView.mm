//
//  B2ScreenView.m
//  BasiliskII
//
//  Created by Jesús A. Álvarez on 09/03/2014.
//  Copyright (c) 2014 namedfork. All rights reserved.
//

#import "B2ScreenView.h"
#include "sysdeps.h"
#include "video.h"
#import "B2AppDelegate.h"

B2ScreenView *sharedScreenView = nil;

@implementation B2ScreenView
{
    CGImageRef screenImage;
    CALayer *videoLayer;
    UIGestureRecognizer *pinchGestureRecognizer;
    CGSize initialSize;
}

- (void)awakeFromNib {
    [super awakeFromNib];
    sharedScreenView = self;
    videoLayer = [CALayer layer];
    [self.layer addSublayer:videoLayer];
}

- (BOOL)hasRetinaVideoMode {
    return [UIDevice currentDevice].userInterfaceIdiom != UIUserInterfaceIdiomPad && (int)[UIScreen mainScreen].scale >= 2;
}

- (void)initVideoModes {
    NSMutableArray<NSValue*> *videoModes = [[NSMutableArray alloc] initWithCapacity:8];
    CGSize screenSize = [UIScreen mainScreen].bounds.size;
    if (screenSize.width < screenSize.height) {
        auto swp = screenSize.width;
        screenSize.width = screenSize.height;
        screenSize.height = swp;
    }
    CGSize landscapeScreenSize = screenSize;
    CGSize portraitScreenSize = CGSizeMake(screenSize.height, screenSize.width);
    
    // current screen size
    NSUserDefaults *defaults = [NSUserDefaults standardUserDefaults];
    [defaults registerDefaults:@{@"videoDepth": @(8), @"videoSize": NSStringFromCGSize(screenSize)}];
    [self addVideoMode:landscapeScreenSize to:videoModes];
    [self addVideoMode:portraitScreenSize to:videoModes];
    if ([self hasRetinaVideoMode]) {
        [self addVideoMode:CGSizeMake(landscapeScreenSize.width * 2, landscapeScreenSize.height * 2) to:videoModes];
        [self addVideoMode:CGSizeMake(portraitScreenSize.width * 2, portraitScreenSize.height * 2) to:videoModes];
    }
    
    // default resolutions
    [self addVideoMode:CGSizeMake(512, 384) to:videoModes];
    [self addVideoMode:CGSizeMake(640, 480) to:videoModes];
    [self addVideoMode:CGSizeMake(800, 600) to:videoModes];
    [self addVideoMode:CGSizeMake(832, 624) to:videoModes];
    [self addVideoMode:CGSizeMake(1024, 768) to:videoModes];
  [self addVideoMode:CGSizeMake(1133, 744) to:videoModes];
  [self addVideoMode:CGSizeMake(1133/2, 744/2) to:videoModes];
  [self addVideoMode:CGSizeMake(1133/3, 744/3) to:videoModes];

  [self addVideoMode:CGSizeMake(512, 496) to:videoModes]; // portrait minus keyboard
  [self addVideoMode:CGSizeMake(512, 672) to:videoModes]; // portrait "full" screen
  [self addVideoMode:CGSizeMake(683, 502) to:videoModes]; // landscape "full" screen

    // custom size
    CGSize customSize = CGSizeFromString([defaults valueForKey:@"videoSize"]);
    _hasCustomVideoMode = [self addVideoMode:customSize to:videoModes];
    _videoModes = [NSArray arrayWithArray:videoModes];
}

- (BOOL)addVideoMode:(CGSize)size to:(NSMutableArray<NSValue*>*)videoModes {
    if (size.width <= 0 || size.height <= 0) {
        return NO;
    }
    NSValue *value = [NSValue valueWithCGSize:size];
    if (![videoModes containsObject:value]) {
        [videoModes addObject:value];
        return YES;
    }
    return NO;
}

- (void)updateCustomSize:(CGSize)customSize {
    NSMutableArray<NSValue*> *videoModes = _videoModes.mutableCopy;
    if (self.hasCustomVideoMode) {
        [videoModes removeLastObject];
        _hasCustomVideoMode = NO;
    }
    _hasCustomVideoMode = [self addVideoMode:customSize to:videoModes];
    _videoModes = [NSArray arrayWithArray:videoModes];
}

- (void)layoutSubviews {
    [super layoutSubviews];
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        [self initVideoModes];
    });

    if (!CGSizeEqualToSize(_screenSize, CGSizeZero)) {
        // Resize screen view after updating constraints
        CGRect viewBounds = self.bounds;
        CGSize screenSize = _screenSize;
        CGFloat screenScale = MAX(screenSize.width / viewBounds.size.width, screenSize.height / viewBounds.size.height);
        _screenBounds = CGRectMake(0, 0, screenSize.width / screenScale, screenSize.height / screenScale);
        _screenBounds.origin.x = (viewBounds.size.width - _screenBounds.size.width)/2;
        _screenBounds = CGRectIntegral(_screenBounds);
        videoLayer.frame = _screenBounds;
        BOOL scaleIsIntegral = (floor(screenScale) == screenScale);
        NSString *filter = kCAFilterNearest;
//        NSString *filter = scaleIsIntegral ? kCAFilterNearest : kCAFilterLinear;
        videoLayer.magnificationFilter = filter;
        videoLayer.minificationFilter = filter;
    }
}

- (void)setScreenSize:(CGSize)screenSize {
    if (![NSThread isMainThread]) {
        dispatch_sync(dispatch_get_main_queue(), ^{
            [self setScreenSize:screenSize];
        });
        return;
    }
    
    _screenSize = screenSize;
    [self updateConstraints];
    [self setNeedsLayout];
}

- (void)updateConstraints {
    [super updateConstraints];
    CGFloat scale = _screenSize.height / self.superview.bounds.size.height;
    BOOL wantsMargins = floor(scale) != scale;
    if (wantsMargins) {
        [NSLayoutConstraint deactivateConstraints:self.fullScreenConstraints];
        [NSLayoutConstraint activateConstraints:self.marginConstraints];
    } else {
        [NSLayoutConstraint deactivateConstraints:self.marginConstraints];
        [NSLayoutConstraint activateConstraints:self.fullScreenConstraints];
    }
}

- (void)updateImage:(CGImageRef)newImage {
    CGImageRef oldImage = screenImage;
    CGImageRelease(oldImage);
    screenImage = newImage;
    if (screenImage != nil) {
        CGImageRetain(screenImage);
    }
    [videoLayer performSelectorOnMainThread:@selector(setContents:) withObject:(__bridge id)screenImage waitUntilDone:NO];
}

@end
