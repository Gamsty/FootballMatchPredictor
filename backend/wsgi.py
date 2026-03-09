"""
WSGI entry point for production
Adds the src/ directory to Python's path so that app.py's
internal imports (database, feature_engineering) resolve correctly.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from app import app # type: ignore

if __name__ == "__main__":
    app.run()