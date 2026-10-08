import json
import os

from PyQt6.QtCore import QStandardPaths

BUILTIN_PRESETS = {
    "Default": {
        "target_tab": 0,
        "format": "PNG",
        "extraction_mode": "Every Frame",
        "custom_fps": 1.0,
        "quality": 95,
        "filter_blur": False,
        "extract_part": False,
        "motion_mode": "MOG2",
        "motion_sensitivity": 20,
        "motion_min_area": 500,
        "gif_format": "GIF",
        "gif_resolution": "Original",
        "gif_fps": "15",
        "gif_quality": 80,
        "fps_subtab": 0,
        "conv_mode": "Standard Resample (Drop / Duplicate frames, preserves speed & audio)",
        "conv_fps": 24.0,
        "conv_codec": "H.264 (Default - libx264)",
        "conv_crf": 20,
        "conv_audio": "Keep Audio (Stream Copy)",
        "interp_fps": 60.0,
        "interp_engine": "FFmpeg Motion Interpolation (MCI)",
        "interp_mc": "aobmc (Adaptive Overlapped Block - Recommended)",
        "interp_me": "epzs (Fast Diamond Search)",
        "interp_scd": True,
        "interp_scd_threshold": 10.0,
        "interp_codec": "H.264 (Default - libx264)",
        "interp_crf": 20,
        "interp_audio": "Keep Audio (Stream Copy)",
    },
    "AI Dataset Collector": {
        "target_tab": 0, # Frame Tab
        "format": "JPEG",
        "extraction_mode": "Custom FPS",
        "custom_fps": 1.0,
        "quality": 95,
        "filter_blur": True,
        "extract_part": False,
        "motion_mode": "MOG2",
        "motion_sensitivity": 20,
        "motion_min_area": 500,
        "gif_format": "GIF",
        "gif_resolution": "Original",
        "gif_fps": "15",
        "gif_quality": 80,
        "fps_subtab": 0,
        "conv_mode": "Standard Resample (Drop / Duplicate frames, preserves speed & audio)",
        "conv_fps": 24.0,
        "conv_codec": "H.264 (Default - libx264)",
        "conv_crf": 20,
        "conv_audio": "Keep Audio (Stream Copy)",
        "interp_fps": 60.0,
        "interp_engine": "FFmpeg Motion Interpolation (MCI)",
        "interp_mc": "aobmc (Adaptive Overlapped Block - Recommended)",
        "interp_me": "epzs (Fast Diamond Search)",
        "interp_scd": True,
        "interp_scd_threshold": 10.0,
        "interp_codec": "H.264 (Default - libx264)",
        "interp_crf": 20,
        "interp_audio": "Keep Audio (Stream Copy)",
    },
    "Discord Reaction GIF": {
        "target_tab": 3, # Animation Tab
        "format": "PNG",
        "extraction_mode": "Every Frame",
        "custom_fps": 1.0,
        "quality": 95,
        "filter_blur": False,
        "extract_part": False,
        "motion_mode": "MOG2",
        "motion_sensitivity": 20,
        "motion_min_area": 500,
        "gif_format": "GIF",
        "gif_resolution": "480p",
        "gif_fps": "15",
        "gif_quality": 80,
        "fps_subtab": 0,
        "conv_mode": "Standard Resample (Drop / Duplicate frames, preserves speed & audio)",
        "conv_fps": 24.0,
        "conv_codec": "H.264 (Default - libx264)",
        "conv_crf": 20,
        "conv_audio": "Keep Audio (Stream Copy)",
        "interp_fps": 60.0,
        "interp_engine": "FFmpeg Motion Interpolation (MCI)",
        "interp_mc": "aobmc (Adaptive Overlapped Block - Recommended)",
        "interp_me": "epzs (Fast Diamond Search)",
        "interp_scd": True,
        "interp_scd_threshold": 10.0,
        "interp_codec": "H.264 (Default - libx264)",
        "interp_crf": 20,
        "interp_audio": "Keep Audio (Stream Copy)",
    },
    "Pristine Wallpaper Grabber": {
        "target_tab": 0, # Frame Tab
        "format": "PNG",
        "extraction_mode": "Every Frame",
        "custom_fps": 1.0,
        "quality": 9,
        "filter_blur": True,
        "extract_part": False,
        "motion_mode": "MOG2",
        "motion_sensitivity": 20,
        "motion_min_area": 500,
        "gif_format": "GIF",
        "gif_resolution": "Original",
        "gif_fps": "15",
        "gif_quality": 80,
        "fps_subtab": 0,
        "conv_mode": "Standard Resample (Drop / Duplicate frames, preserves speed & audio)",
        "conv_fps": 24.0,
        "conv_codec": "H.264 (Default - libx264)",
        "conv_crf": 20,
        "conv_audio": "Keep Audio (Stream Copy)",
        "interp_fps": 60.0,
        "interp_engine": "FFmpeg Motion Interpolation (MCI)",
        "interp_mc": "aobmc (Adaptive Overlapped Block - Recommended)",
        "interp_me": "epzs (Fast Diamond Search)",
        "interp_scd": True,
        "interp_scd_threshold": 10.0,
        "interp_codec": "H.264 (Default - libx264)",
        "interp_crf": 20,
        "interp_audio": "Keep Audio (Stream Copy)",
    },
    "Security Activity Highlights": {
        "target_tab": 2, # Motion Tab
        "format": "PNG",
        "extraction_mode": "Every Frame",
        "custom_fps": 1.0,
        "quality": 95,
        "filter_blur": False,
        "extract_part": False,
        "motion_mode": "MOG2",
        "motion_sensitivity": 40,
        "motion_min_area": 500,
        "gif_format": "GIF",
        "gif_resolution": "Original",
        "gif_fps": "15",
        "gif_quality": 80,
        "fps_subtab": 0,
        "conv_mode": "Standard Resample (Drop / Duplicate frames, preserves speed & audio)",
        "conv_fps": 24.0,
        "conv_codec": "H.264 (Default - libx264)",
        "conv_crf": 20,
        "conv_audio": "Keep Audio (Stream Copy)",
        "interp_fps": 60.0,
        "interp_engine": "FFmpeg Motion Interpolation (MCI)",
        "interp_mc": "aobmc (Adaptive Overlapped Block - Recommended)",
        "interp_me": "epzs (Fast Diamond Search)",
        "interp_scd": True,
        "interp_scd_threshold": 10.0,
        "interp_codec": "H.264 (Default - libx264)",
        "interp_crf": 20,
        "interp_audio": "Keep Audio (Stream Copy)",
    },
    "Modern Web Demo (WebP)": {
        "target_tab": 3, # Animation Tab
        "format": "PNG",
        "extraction_mode": "Every Frame",
        "custom_fps": 1.0,
        "quality": 95,
        "filter_blur": False,
        "extract_part": False,
        "motion_mode": "MOG2",
        "motion_sensitivity": 20,
        "motion_min_area": 500,
        "gif_format": "Animated WebP",
        "gif_resolution": "720p",
        "gif_fps": "24",
        "gif_quality": 80,
        "fps_subtab": 0,
        "conv_mode": "Standard Resample (Drop / Duplicate frames, preserves speed & audio)",
        "conv_fps": 24.0,
        "conv_codec": "H.264 (Default - libx264)",
        "conv_crf": 20,
        "conv_audio": "Keep Audio (Stream Copy)",
        "interp_fps": 60.0,
        "interp_engine": "FFmpeg Motion Interpolation (MCI)",
        "interp_mc": "aobmc (Adaptive Overlapped Block - Recommended)",
        "interp_me": "epzs (Fast Diamond Search)",
        "interp_scd": True,
        "interp_scd_threshold": 10.0,
        "interp_codec": "H.264 (Default - libx264)",
        "interp_crf": 20,
        "interp_audio": "Keep Audio (Stream Copy)",
    },
    "Cinematic 24 FPS Conversion": {
        "target_tab": 4, # FPS & Interpolation Tab
        "fps_subtab": 0, # Framerate Conversion
        "format": "PNG",
        "extraction_mode": "Every Frame",
        "custom_fps": 1.0,
        "quality": 95,
        "filter_blur": False,
        "extract_part": False,
        "motion_mode": "MOG2",
        "motion_sensitivity": 20,
        "motion_min_area": 500,
        "gif_format": "GIF",
        "gif_resolution": "Original",
        "gif_fps": "15",
        "gif_quality": 80,
        "conv_mode": "Standard Resample (Drop / Duplicate frames, preserves speed & audio)",
        "conv_fps": 24.0,
        "conv_codec": "H.264 (Default - libx264)",
        "conv_crf": 20,
        "conv_audio": "Keep Audio (Stream Copy)",
        "interp_fps": 60.0,
        "interp_engine": "FFmpeg Motion Interpolation (MCI)",
        "interp_mc": "aobmc (Adaptive Overlapped Block - Recommended)",
        "interp_me": "epzs (Fast Diamond Search)",
        "interp_scd": True,
        "interp_scd_threshold": 10.0,
        "interp_codec": "H.264 (Default - libx264)",
        "interp_crf": 20,
        "interp_audio": "Keep Audio (Stream Copy)",
    },
    "Smooth 60 FPS Interpolation": {
        "target_tab": 4, # FPS & Interpolation Tab
        "fps_subtab": 1, # Frame Interpolation
        "format": "PNG",
        "extraction_mode": "Every Frame",
        "custom_fps": 1.0,
        "quality": 95,
        "filter_blur": False,
        "extract_part": False,
        "motion_mode": "MOG2",
        "motion_sensitivity": 20,
        "motion_min_area": 500,
        "gif_format": "GIF",
        "gif_resolution": "Original",
        "gif_fps": "15",
        "gif_quality": 80,
        "fps_subtab": 1,
        "conv_mode": "Standard Resample (Drop / Duplicate frames, preserves speed & audio)",
        "conv_fps": 24.0,
        "conv_codec": "H.264 (Default - libx264)",
        "conv_crf": 20,
        "conv_audio": "Keep Audio (Stream Copy)",
        "interp_fps": 60.0,
        "interp_engine": "FFmpeg Motion Interpolation (MCI)",
        "interp_mc": "aobmc (Adaptive Overlapped Block - Recommended)",
        "interp_me": "epzs (Fast Diamond Search)",
        "interp_scd": True,
        "interp_scd_threshold": 10.0,
        "interp_codec": "H.264 (Default - libx264)",
        "interp_crf": 20,
        "interp_audio": "Keep Audio (Stream Copy)",
    }
}

class PresetManager:
    def __init__(self):
        config_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
        if not os.path.exists(config_dir):
            os.makedirs(config_dir)
        self.preset_file = os.path.join(config_dir, "presets.json")
        self.user_presets = self._load_user_presets()

    def _load_user_presets(self):
        if os.path.exists(self.preset_file):
            try:
                with open(self.preset_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def save_user_presets(self):
        try:
            with open(self.preset_file, 'w', encoding='utf-8') as f:
                json.dump(self.user_presets, f, indent=4)
        except Exception as e:
            print(f"Failed to save presets: {e}")

    def get_all_preset_names(self):
        names = list(BUILTIN_PRESETS.keys())
        user_names = sorted(self.user_presets.keys())
        return names + user_names

    def get_preset(self, name):
        if name in BUILTIN_PRESETS:
            return BUILTIN_PRESETS[name]
        return self.user_presets.get(name)

    def is_builtin(self, name):
        return name in BUILTIN_PRESETS

    def add_preset(self, name, state):
        if name in BUILTIN_PRESETS:
            raise ValueError("Cannot overwrite a built-in preset.")
        self.user_presets[name] = state
        self.save_user_presets()

    def remove_preset(self, name):
        if name in self.user_presets:
            del self.user_presets[name]
            self.save_user_presets()
            return True
        return False
