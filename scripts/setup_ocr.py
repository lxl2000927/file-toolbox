"""Install pinned official Tesseract fast language data (runtime stays offline)."""
from pathlib import Path
import hashlib
import urllib.request

REVISION = '87416418657359cb625c412a48b6e1d6d41c29bd'
FILES = {
    'eng.traineddata': '7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2',
    'chi_sim.traineddata': 'a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730',
    'LICENSE': 'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30',
}


def main():
    root = Path(__file__).resolve().parents[1] / 'assets' / 'tessdata'
    root.mkdir(parents=True, exist_ok=True)
    for name, checksum in FILES.items():
        destination = root / name
        if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() == checksum:
            print(f'OK {name}')
            continue
        with urllib.request.urlopen(f'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{REVISION}/{name}', timeout=60) as response:
            data = response.read(20 * 1024 * 1024)
        if hashlib.sha256(data).hexdigest() != checksum:
            raise RuntimeError(f'Checksum mismatch: {name}')
        temporary = destination.with_suffix('.download')
        temporary.write_bytes(data)
        temporary.replace(destination)
        print(f'Installed {name}')


if __name__ == '__main__':
    main()
