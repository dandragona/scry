// Test driver for tests/test_web_markdown.py: read a JSON array of markdown
// strings from stdin, render each through the REAL scry_web markdown.js, and
// emit a JSON array of HTML strings. Kept tiny + dependency-free; the Python
// test skips entirely when node isn't installed.
import { renderMarkdown } from "../scry_web/static/markdown.js";

let buf = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (buf += c));
process.stdin.on("end", () => {
  const inputs = JSON.parse(buf);
  process.stdout.write(JSON.stringify(inputs.map((s) => renderMarkdown(s))));
});
