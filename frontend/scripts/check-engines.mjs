import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const declaredNodeEngine = '^20.19.0 || ^22.13.0 || >=24.0.0';
const checkedNodeLines = ['20.19.0', '22.13.0', '24.0.0'];

function readJson(relativePath) {
  return JSON.parse(readFileSync(fileURLToPath(new URL(relativePath, import.meta.url)), 'utf8'));
}

function parseVersion(value) {
  return value.replace(/^v/, '').split('.').map(Number);
}

function compareVersions(left, right) {
  for (let index = 0; index < 3; index += 1) {
    if (left[index] !== right[index]) return left[index] - right[index];
  }
  return 0;
}

function satisfies(version, range) {
  return range.split('||').some((alternative) => {
    const normalized = alternative.trim();
    if (normalized.startsWith('>=')) {
      return compareVersions(version, parseVersion(normalized.slice(2))) >= 0;
    }
    if (normalized.startsWith('^')) {
      const minimum = parseVersion(normalized.slice(1));
      const upperBound = [minimum[0] + 1, 0, 0];
      return compareVersions(version, minimum) >= 0 && compareVersions(version, upperBound) < 0;
    }
    return false;
  });
}

const packageJson = readJson('../package.json');
assert.equal(packageJson.engines?.node, declaredNodeEngine);

for (const packageName of ['vite', '@vitejs/plugin-react', 'jsdom']) {
  const packageEngine = readJson(`../node_modules/${packageName}/package.json`).engines?.node;
  assert.ok(packageEngine, `${packageName} must declare a Node engine`);
  assert.ok(
    checkedNodeLines.some(
      (version) => satisfies(parseVersion(version), declaredNodeEngine) && satisfies(parseVersion(version), packageEngine),
    ),
    `${declaredNodeEngine} must intersect ${packageName} (${packageEngine})`,
  );
}

assert.ok(satisfies(parseVersion(process.version), declaredNodeEngine));
console.log(`Node engine contract OK: ${declaredNodeEngine}`);
