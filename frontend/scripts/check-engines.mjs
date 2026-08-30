import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import semver from 'semver';

const requiredTools = ['vite', '@vitejs/plugin-react', 'jsdom', 'vitest'];

function readJson(relativePath) {
  return JSON.parse(readFileSync(fileURLToPath(new URL(relativePath, import.meta.url)), 'utf8'));
}

/**
 * Verify that every Node version admitted by the project is supported by each
 * installed frontend build/test tool. semver.subset checks the complete range,
 * including gaps between OR alternatives.
 */
export function validateEngineContract(projectRange, toolEngines) {
  const normalizedProjectRange = semver.validRange(projectRange);
  if (!normalizedProjectRange) {
    throw new Error(`Invalid project Node engine range: ${projectRange}`);
  }

  for (const [packageName, toolRange] of Object.entries(toolEngines)) {
    const normalizedToolRange = semver.validRange(toolRange);
    if (!normalizedToolRange) {
      throw new Error(`Invalid Node engine range for ${packageName}: ${toolRange}`);
    }
    if (!semver.subset(normalizedProjectRange, normalizedToolRange)) {
      throw new Error(
        `Project Node engine range ${projectRange} is not a subset of ${packageName} Node engine range ${toolRange}`,
      );
    }
  }
}

function packageEngine(packageName) {
  return readJson(`../node_modules/${packageName}/package.json`).engines?.node;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const packageJson = readJson('../package.json');
  const projectRange = packageJson.engines?.node;
  assert.ok(projectRange, 'frontend/package.json must declare engines.node');

  const toolEngines = Object.fromEntries(
    requiredTools
      .map((packageName) => [packageName, packageEngine(packageName)])
      .filter(([, range]) => range),
  );
  validateEngineContract(projectRange, toolEngines);
  console.log(`Node engine contract OK: ${projectRange}`);
}
