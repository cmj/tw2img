import os
import shutil
from pathlib import Path

def install_config():
    config_dir = Path.home() / ".config" / "tw2img"
    config_dst = config_dir / "tw2img.conf"
    config_src = Path(__file__).parent / "tw2img.conf"

    config_dir.mkdir(parents=True, exist_ok=True)

    if config_dst.exists():
        print(f"Config already exists, skipping: {config_dst}")
    else:
        shutil.copy(config_src, config_dst)
        print(f"Config installed to: {config_dst}")
