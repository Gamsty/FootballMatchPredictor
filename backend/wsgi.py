"""
WSGI entry point for production
"""

from src.app import app

if __name__ == "__main__":
    app.run()