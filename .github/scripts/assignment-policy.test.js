// Run with: node --test .github/scripts/assignment-policy.test.js
const assert = require("node:assert/strict");
const { test } = require("node:test");
const {
  linkedIssues,
  replyToIssueComment,
  reviewPullRequest,
} = require("./assignment-policy.js");

const REPO = "https://github.com/owner/repo";
const user = (login, type = "User") => ({ login, type });
const issue = (labels, assignees = [], extra = {}) => ({
  number: 7,
  state: "open",
  labels: labels.map((name) => ({ name })),
  assignees: assignees.map((login) => ({ login })),
  ...extra,
});
const comment = (login, association = "NONE") => ({
  user: user(login),
  author_association: association,
});

test("good first issue, unassigned: explains that a maintainer assigns it", () => {
  const reply = replyToIssueComment({
    issue: issue(["good first issue"]),
    comment: comment("newbie"),
    repoUrl: REPO,
  });
  assert.match(reply, /@newbie/);
  assert.match(reply, /assigned by a maintainer/);
  assert.match(reply, /CONTRIBUTING\.md#picking-an-issue/);
  assert.match(reply, /<!-- assignment-policy:claim:newbie -->/);
});

test("already assigned to someone else: points to other issues", () => {
  const reply = replyToIssueComment({
    issue: issue(["good first issue"], ["first"]),
    comment: comment("second"),
    repoUrl: REPO,
  });
  assert.match(reply, /already assigned to @first/);
  assert.match(reply, /labels\/good%20first%20issue/);
});

test("help wanted, unassigned: open to anyone", () => {
  const reply = replyToIssueComment({
    issue: issue(["help wanted"]),
    comment: comment("someone"),
    repoUrl: REPO,
  });
  assert.match(reply, /open to anyone/);
});

test("stays quiet for maintainers, bots, assignees, other labels, PRs and repeats", () => {
  const quiet = [
    { issue: issue(["good first issue"]), comment: comment("me", "OWNER") },
    { issue: issue(["good first issue"]), comment: comment("helper", "COLLABORATOR") },
    { issue: issue(["good first issue"]), comment: { user: user("bot", "Bot") } },
    { issue: issue(["good first issue"], ["Mine"]), comment: comment("mine") },
    { issue: issue(["bug"]), comment: comment("x") },
    { issue: issue(["good first issue"], [], { pull_request: {} }), comment: comment("x") },
    { issue: issue(["good first issue"], [], { state: "closed" }), comment: comment("x") },
    {
      issue: issue(["good first issue"]),
      comment: comment("Again"),
      previousBodies: ["hi\n<!-- assignment-policy:claim:again -->"],
    },
  ];
  for (const args of quiet) assert.equal(replyToIssueComment({ repoUrl: REPO, ...args }), null);
});

test("linkedIssues finds closing keywords only", () => {
  assert.deepEqual(linkedIssues("Closes #12 and fixes #3; resolved #12. See #99."), [3, 12]);
  assert.deepEqual(linkedIssues(""), []);
  assert.deepEqual(linkedIssues(null), []);
});

const pr = (login, association = "CONTRIBUTOR") => ({
  user: user(login),
  author_association: association,
});

test("PR for an unassigned or someone else's good first issue gets a note", () => {
  const note = reviewPullRequest({
    pr: pr("eager"),
    issues: [
      { ...issue(["good first issue"]), number: 1 },
      { ...issue(["good first issue"], ["other"]), number: 2 },
    ],
    mergedCount: 0,
    repoUrl: REPO,
  });
  assert.match(note, /#1 is a good first issue that isn't assigned/);
  assert.match(note, /#2 is a good first issue assigned to someone else/);
  assert.match(note, /<!-- assignment-policy:pr:eager -->/);
});

test("experienced contributors are pointed to help wanted", () => {
  const note = reviewPullRequest({
    pr: pr("regular"),
    issues: [issue(["good first issue"], ["regular"])],
    mergedCount: 2,
    repoUrl: REPO,
  });
  assert.match(note, /2 merged PRs/);
  assert.match(note, /help%20wanted/);
});

test("PR is fine: assigned newcomer, help wanted, maintainer, or already noted", () => {
  const fine = [
    { pr: pr("newbie"), issues: [issue(["good first issue"], ["Newbie"])], mergedCount: 0 },
    { pr: pr("anyone"), issues: [issue(["help wanted"])], mergedCount: 5 },
    { pr: pr("me", "OWNER"), issues: [issue(["good first issue"])], mergedCount: 9 },
    {
      pr: pr("eager"),
      issues: [issue(["good first issue"])],
      mergedCount: 0,
      previousBodies: ["<!-- assignment-policy:pr:eager -->"],
    },
  ];
  for (const args of fine) assert.equal(reviewPullRequest({ repoUrl: REPO, ...args }), null);
});
