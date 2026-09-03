import os
import sys

# Set directory pointers
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OMR_V2_DIR = os.path.join(BASE_DIR, "omr-v2")

if OMR_V2_DIR not in sys.path:
    sys.path.insert(0, OMR_V2_DIR)

# Switch working directory to omr-v2 for consistent local path resolution
os.chdir(OMR_V2_DIR)

# Execute the modern Telkom University OMR v2 application
target_app = os.path.join(OMR_V2_DIR, "app.py")
with open(target_app, "r", encoding="utf-8") as f:
    code = compile(f.read(), target_app, "exec")
    exec(code, globals())
