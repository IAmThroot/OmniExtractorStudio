import os

# Ensure Qt uses the headless offscreen plugin in CI environments
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
