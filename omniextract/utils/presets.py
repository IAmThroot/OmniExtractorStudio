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
        "gif_quality": 80
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
        "gif_quality": 80
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
        "gif_quality": 80
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
        "gif_quality": 80
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
        "gif_quality": 80
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
        "gif_quality": 80
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
