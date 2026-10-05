// Verify the five release assets and the updater's size/digest against actual bytes.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const zlib = require('node:zlib');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const desktop = path.join(root, 'electron-app');
const asar = require(path.join(desktop, 'node_modules/@electron/asar'));
const yaml = require(path.join(desktop, 'node_modules/js-yaml'));

async function main() {
  const version = require(path.join(desktop, 'package.json')).version;
  const release = path.join(desktop, 'release');
  const manifest = yaml.load(fs.readFileSync(path.join(release, 'latest.yml'), 'utf8'));
  const metadata = JSON.parse(asar.extractFile(path.join(release, 'win-unpacked/resources/app.asar'), 'package.json'));
  assert.equal(manifest.version, version); assert.equal(metadata.version, version);
  const artifacts = [`File.Toolbox-${version}-x64-setup.exe`, `File.Toolbox-${version}-x64-portable.exe`,
    `File.Toolbox-${version}-x64.zip`, `File.Toolbox-${version}-x64-setup.exe.blockmap`, 'latest.yml'];
  const records = [];
  for (const name of artifacts) {
    const sha256 = crypto.createHash('sha256'), sha512 = crypto.createHash('sha512');
    for await (const chunk of fs.createReadStream(path.join(release, name))) { sha256.update(chunk); sha512.update(chunk); }
    records.push({ name, size: fs.statSync(path.join(release, name)).size, sha256: sha256.digest('hex'), sha512: sha512.digest('base64') });
  }
  const setup = records.find(a => a.name === manifest.path);
  assert.ok(setup); assert.equal(manifest.files.length, 1); assert.equal(manifest.files[0].url, manifest.path);
  assert.equal(setup.size, manifest.files[0].size); assert.equal(setup.sha512, manifest.sha512); assert.equal(setup.sha512, manifest.files[0].sha512);
  // External NSIS blockmaps use gzip; embedded blockmaps use raw deflate.
  const blockmap = JSON.parse(zlib.gunzipSync(fs.readFileSync(path.join(release, `${manifest.path}.blockmap`))));
  assert.equal(blockmap.version, '2'); assert.ok(blockmap.files.length);
  const report = path.join(root, `acceptance-samples/release-${version}/artifact-manifest.json`);
  fs.mkdirSync(path.dirname(report), { recursive: true });
  fs.writeFileSync(report, JSON.stringify({ passed: true, version, records,
    checks: ['packaged app version', 'updater installer path and size', 'SHA512 updater integrity', 'external blockmap valid'] }, null, 2));
  console.log(JSON.stringify({ passed: true, version, artifacts: records.map(({ name, size, sha256 }) => ({ name, size, sha256 })) }, null, 2));
}
main().catch(error => { console.error(error); process.exitCode = 1; });
