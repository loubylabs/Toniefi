import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { installDom } from "./mini-dom.mjs";

const harnessPath = fileURLToPath(new URL("./mini-dom.mjs", import.meta.url));

test("mini-dom elements carry the ownerDocument back-reference the fix below has to defeat", () => {
  const { document, workspace } = installDom();
  const node = document.createElement("div");
  workspace.append(node);
  assert.equal(node.ownerDocument, document);
  assert.equal(document.body.contains(node), true);
});

test("a failing assert.equal between two different mini-dom nodes throws promptly, not a hang", () => {
  // MiniElement nodes hold an ownerDocument back-reference, and
  // document.body reaches back down into the whole rendered tree, so any
  // two elements in the same document sit on a densely cross-linked graph,
  // not a clean tree. A failing assert.equal() hands both nodes to
  // node:assert's diff builder, which calls util.inspect with no depth cap.
  // util.inspect's cycle guard only remembers nodes already open on the
  // current path, not nodes visited anywhere else in the same call, so
  // ownerDocument gave that walk a way back into the whole tree from every
  // node in it: the same shared subtrees got freshly re-walked over and
  // over and the process hung instead of failing. See the constructor in
  // mini-dom.mjs, which fixes this by making ownerDocument non-enumerable
  // so inspection never follows it; the property still reads normally for
  // every other caller.
  //
  // This has to run in a subprocess with a hard timeout. The hang is
  // synchronous: nothing yields, so there is no way to time it out from
  // inside this same process, and calling assert.equal directly here would
  // hang node --test itself if the fix ever regressed. spawnSync's own
  // timeout kills the child instead, so this test fails fast and cleanly
  // even if the hazard comes back.
  const childCode = `
    import assert from "node:assert/strict";
    import { installDom } from ${JSON.stringify(harnessPath)};

    // Two nodes on opposite sides of a small nested list: one shallow
    // sibling, one buried a few levels deep among many rows and cells. This
    // shape is what turned a modest node count into a genuine hang before
    // the fix (proven against the unfixed harness with a 15-second kill).
    const { document, workspace } = installDom();
    const outerList = document.createElement("div");
    workspace.append(outerList);
    const shallowNode = document.createElement("button");
    outerList.append(shallowNode);
    const detailHost = document.createElement("div");
    outerList.append(detailHost);
    const detailList = document.createElement("div");
    detailHost.append(detailList);
    let deepNode = null;
    for (let r = 0; r < 15; r++) {
      const row = document.createElement("div");
      detailList.append(row);
      for (let c = 0; c < 5; c++) {
        const cell = document.createElement("div");
        row.append(cell);
        const button = document.createElement("button");
        cell.append(button);
        if (!deepNode) deepNode = button;
      }
    }

    try {
      assert.equal(shallowNode, deepNode);
      console.log("DID_NOT_THROW");
    } catch (err) {
      console.log(err instanceof assert.AssertionError ? "THREW_ASSERTION_ERROR" : \`THREW_OTHER: \${err}\`);
    }
  `;

  const start = Date.now();
  const result = spawnSync(process.execPath, ["--input-type=module", "-e", childCode], {
    timeout: 8000,
    killSignal: "SIGKILL",
    encoding: "utf8",
  });
  const elapsed = Date.now() - start;

  // A regression brings the hang back, and spawnSync's timeout is what
  // turns that back into a clean, fast failure of this assertion instead of
  // a hang of the whole suite: a killed child reports a signal, not null.
  assert.equal(result.signal, null, `comparison did not return within the timeout (stdout: ${result.stdout || "<none>"})`);
  assert.equal(result.status, 0, `child process failed unexpectedly: ${result.stderr}`);
  assert.match(result.stdout, /THREW_ASSERTION_ERROR/);
  assert.ok(elapsed < 3000, `comparison took ${elapsed}ms; expected well under the 8s timeout`);
});
