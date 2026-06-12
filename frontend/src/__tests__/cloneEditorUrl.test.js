/**
 * Regression guard: every internal Link/href in src/pages and src/components
 * that references a clone editor URL must use the plural `/clones/`,
 * matching the route declared in App.js: `/clones/:cloneId/edit`.
 *
 * This test caught a real "Edit button does nothing" bug on My Space where
 * the link was `/clone/${id}/edit` (singular). It would silently render a
 * 404 / blank page because no route matched.
 */
const fs = require("fs");
const path = require("path");

function walk(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name === "build") continue;
      walk(full, out);
    } else if (/\.(jsx?|tsx?)$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

describe("clone editor URL consistency", () => {
  test("no source file links to the singular /clone/...edit", () => {
    const root = path.join(__dirname, "..");
    const files = walk(root);
    const offenders = [];

    // Match `to={...'/clone/' + ... + '/edit'...}` or string literals
    // like "/clone/${id}/edit". The negative case `/clones/` must NOT match.
    const re = /\/clone\/[^"`'/]*\/edit/;

    for (const f of files) {
      // Exclude this test file itself (its source contains the pattern as
      // a string literal we're hunting for in *other* files).
      if (f.includes("cloneEditorUrl.test")) continue;
      const txt = fs.readFileSync(f, "utf8");
      // Quick exclude — only files that mention `/edit` and `clone`
      if (!txt.includes("/edit")) continue;
      // Match the legacy singular form
      const matches = txt.match(/`\/clone\/\$\{[^`]*\}\/edit`/g)
        || txt.match(/"\/clone\/[^"]*\/edit"/g);
      if (matches && matches.length) {
        offenders.push({ file: f, hits: matches });
      }
      // Also catch the template-literal shape directly
      const tpl = txt.match(/\/clone\/\$\{[A-Za-z_.]+\}\/edit/g);
      if (tpl && tpl.length) {
        offenders.push({ file: f, hits: tpl });
      }
    }
    expect(offenders).toEqual([]);
  });
});
