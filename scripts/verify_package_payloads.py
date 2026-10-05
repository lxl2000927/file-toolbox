"""Check ZIP CRCs and prove packaged payloads match the smoke-tested binaries."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(stream):
    value = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        value.update(block)
    return value.hexdigest()


def main():
    version = json.loads((ROOT / 'electron-app/package.json').read_text('utf-8'))['version']
    release = ROOT / 'electron-app/release'
    records = []
    with zipfile.ZipFile(release / f'File.Toolbox-{version}-x64.zip') as archive:
        assert archive.testzip() is None, 'ZIP CRC mismatch'
        for name in ['File Toolbox.exe', 'resources/app.asar', 'resources/engine/engine.exe']:
            with archive.open(name) as stream:
                archived = digest(stream)
            with (release / 'win-unpacked' / name).open('rb') as stream:
                assert archived == digest(stream), f'ZIP differs from unpacked payload: {name}'
            if name.endswith('/engine.exe'):
                with (ROOT / 'engine/dist/engine.exe').open('rb') as stream:
                    assert archived == digest(stream), 'Packaged engine differs from tested frozen engine'
            records.append({'name': name, 'sha256': archived})
    report = {'passed': True, 'version': version, 'zip_crc': True, 'records': records}
    destination = ROOT / f'acceptance-samples/release-{version}/payload-verification.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
