"""PhD-Radar entrypoint. Start: python run.py"""
import threading, webbrowser, uvicorn
from backend.api import app
from backend.config_store import DATA, VERSION, cfg, load_universities

if __name__ == "__main__":
    print(f"PhD-Radar v{VERSION} | {len(cfg()['sources'])} sources | {len(load_universities())} universities | Data dir: {DATA}")
    threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:8765")).start()
    uvicorn.run(app, host="127.0.0.1", port=8765)
