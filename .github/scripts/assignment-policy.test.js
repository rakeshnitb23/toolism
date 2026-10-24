// Run with: node --test .github/scripts/assignment-policy.test.js
const assert = require("node:assert/strict");
const { test } = require("node:test");
const {
  NEWCOMER_MERGED_PR_LIMIT,
  linkedIssues,
  prsForIssue,
  replyToIssueComment,
  reviewPullRequest,
} = require("./assignment-policy.js");

const REPO = "https://github.com/owner/repo";
const user = (login, type = "User") => ({ login, type });
const issue = (labels, extra = {}) => ({
  number: 7,
  state: "open",
  labels: labels.map((name) => ({ name })),
  ...extra,
});
const comment = (login, association = "NONE") => ({
  user: user(login),
  author_association: association,
});
const openPr = (number, login, body, created_at, association = "CONTRIBUTOR") => ({
  number,
  user: user(login),
  author_association: association,
  body,
  created_at,
});

test("the newcomer limit is 5 merged PRs", () => {
  assert.equal(NEWCOMER_MERGED_PR_LIMIT, 5);
});

test("good first issue with no PR yet: go ahead and open one, no assignment", () => {
  const reply = replyToIssueComment({
    issue: issue(["good first issue"]),
    comment: comment("newbie"),
    repoUrl: REPO,
  });
  assert.match(reply, /@newbie/);
  assert.match(reply, /No need to wait for assignment/);
  assert.match(reply, /Closes #7/);
  assert.match(reply, /first one that's ready/);
  assert.match(reply, /fewer than 5 merged/);
  assert.doesNotMatch(reply, /wait until/);
  assert.match(reply, /<!-- assignment-policy:claim:newbie -->/);
});

test("help wanted: same invitation, without the newcomer line", () => {
  const reply = replyToIssueComment({
    issue: issue(["help wanted"]),
    comment: comment("someone"),
    repoUrl: REPO,
    mergedCount: 12,
  });
  assert.match(reply, /go ahead and open a pull request/);
  assert.doesNotMatch(reply, /fewer than 5/);
});

test("an existing open PR for the issue is pointed out", () => {
  const reply = replyToIssueComment({
    issue: issue(["good first issue"]),
    comment: comment("late"),
    repoUrl: REPO,
    openPrs: [openPr(40, "early", "Closes #7", "2026-01-01T00:00:00Z")],
  });
  assert.match(reply, /already has an open PR: #40 by @early/);
});

test("experienced contributors are pointed to help wanted", () => {
  const reply = replyToIssueComment({
    issue: issue(["good first issue"]),
    comment: comment("regular"),
    repoUrl: REPO,
    mergedCount: 5,
  });
  assert.match(reply, /5 merged PRs/);
  assert.match(reply, /help%20wanted/);
});

test("stays quiet for maintainers, bots, other labels, PRs, closed issues and repeats", () => {
  const quiet = [
    { issue: issue(["good first issue"]), comment: comment("me", "OWNER") },
    { issue: issue(["good first issue"]), comment: comment("helper", "COLLABORATOR") },
    { issue: issue(["good first issue"]), comment: { user: user("bot", "Bot") } },
    { issue: issue(["bug"]), comment: comment("x") },
    { issue: issue(["good first issue"], { pull_request: {} }), comment: comment("x") },
    { issue: issue(["good first issue"], { state: "closed" }), comment: comment("x") },
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

test("prsForIssue returns the PRs linking an issue, oldest first", () => {
  const prs = [
    openPr(9, "b", "Fixes #7", "2026-01-02T00:00:00Z"),
    openPr(8, "a", "Closes #7", "2026-01-01T00:00:00Z"),
    openPr(10, "c", "Closes #8", "2026-01-01T00:00:00Z"),
  ];
  assert.deepEqual(prsForIssue(7, prs).map((pr) => pr.number), [8, 9]);
});

test("a later PR for the same issue gets a note naming the earlier one", () => {
  const first = openPr(40, "early", "Closes #7", "2026-01-01T00:00:00Z");
  const second = openPr(47, "late", "Closes #7", "2026-01-02T00:00:00Z");
  const note = reviewPullRequest({
    pr: second,
    issues: [issue(["good first issue"])],
    openPrs: [first, second],
    mergedCount: 0,
    repoUrl: REPO,
  });
  assert.match(note, /#7 already has an earlier open PR: #40 by @early/);
  assert.match(note, /<!-- assignment-policy:pr:late -->/);
  // The earlier PR itself is not flagged.
  assert.equal(
    reviewPullRequest({
      pr: first,
      issues: [issue(["good first issue"])],
      openPrs: [first, second],
      mergedCount: 0,
      repoUrl: REPO,
    }),
    null,
  );
});

test("a good first issue PR from an experienced contributor gets a note", () => {
  const pr = openPr(50, "regular", "Closes #7", "2026-01-01T00:00:00Z");
  const note = reviewPullRequest({
    pr,
    issues: [issue(["good first issue"])],
    openPrs: [pr],
    mergedCount: 5,
    repoUrl: REPO,
  });
  assert.match(note, /5 merged PRs/);
});

test("no note: first PR from a newcomer, help wanted, maintainer, or already noted", () => {
  const pr = openPr(50, "newbie", "Closes #7", "2026-01-01T00:00:00Z");
  const fine = [
    { pr, issues: [issue(["good first issue"])], openPrs: [pr], mergedCount: 4 },
    { pr, issues: [issue(["help wanted"])], openPrs: [pr], mergedCount: 30 },
    {
      pr: openPr(51, "me", "Closes #7", "2026-01-02T00:00:00Z", "OWNER"),
      issues: [issue(["good first issue"])],
      openPrs: [pr],
      mergedCount: 9,
    },
    {
      pr: openPr(52, "late", "Closes #7", "2026-01-02T00:00:00Z"),
      issues: [issue(["good first issue"])],
      openPrs: [pr],
      mergedCount: 0,
      previousBodies: ["<!-- assignment-policy:pr:late -->"],
    },
  ];
  for (const args of fine) assert.equal(reviewPullRequest({ repoUrl: REPO, ...args }), null);
});
