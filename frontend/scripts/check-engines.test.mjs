import assert from 'node:assert/strict';
import test from 'node:test';
import { validateEngineContract } from './check-engines.mjs';

const toolEngines = {
  vite: '^20.19.0 || ^22.12.0 || >=24.0.0',
  '@vitejs/plugin-react': '^20.19.0 || ^22.12.0 || >=24.0.0',
  jsdom: '^20.19.0 || ^22.13.0 || >=24.0.0',
};

test('accepts a project range that is fully supported by each tool', () => {
  assert.doesNotThrow(() => validateEngineContract('^20.19.0 || ^22.13.0 || >=24.0.0', toolEngines));
});

test('rejects the old broad range because it includes unsupported Node releases', () => {
  assert.throws(
    () => validateEngineContract('>=20.19 || >=22.12', { jsdom: toolEngines.jsdom }),
    /Project Node engine range >=20\.19 \|\| >=22\.12 is not a subset of jsdom/,
  );
});
