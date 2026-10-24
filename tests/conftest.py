"""
Pytest configuration for WuWei Core
"""
import sys
from pathlib import Path

# Thêm root của WuWei Core vào sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
