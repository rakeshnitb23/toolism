// Issue assignment policy, enforced by .github/workflows/assignment-policy.yml.
// The rules are written for contributors in CONTRIBUTING.md ("Picking an issue"). This module
// only decides *what to say*; the workflow does the GitHub API calls. Tested with
// `node --test .github/scripts/assignment-policy.test.js`.

const GOOD_FIRST_ISSUE = "good first issue";
const HELP_WANTED = "help wanted";
const MAINTAINER_ASSOCIATIONS = new Set(["OWNER", "MEMBER", "COLLABORATOR"]);
// Contributors with this many merged PRs are past "first issue" territory.
const NEWCOMER_MERGED_PR_LIMIT = 2;

const marker = (kind, login) => `<!-- assignment-policy:${kind}:${login.toLowerCase()} -->`;

const policyUrl = (repoUrl) => `${repoUrl}/blob/master/CONTRIBUTING.md#picking-an-issue`;

function labelNames(issue) {
  return (issue.labels || []).map((label) => (typeof label === "string" ? label : label.name));
}

function isMaintainer(association) {
  return MAINTAINER_ASSOCIATIONS.has(association);
}

/**
 * Reply to a comment on an issue, or return null to stay quiet.
 * `issue` and `comment` are webhook payload objects; `previousBodies` are the bodies of the
 * issue's existing comments, so each person gets at most one reply per issue.
 */
function replyToIssueComment({ issue, comment, repoUrl, previousBodies = [] }) {
  const login = comment.user.login;
  if (comment.user.type === "Bot" || isMaintainer(comment.author_association)) return null;
  if (issue.pull_request || issue.state !== "open") return null;
  const labels = labelNames(issue);
  const goodFirst = labels.includes(GOOD_FIRST_ISSUE);
  const helpWanted = labels.includes(HELP_WANTED);
  if (!goodFirst && !helpWanted) return null;

  const tag = marker("claim", login);
  if (previousBodies.some((body) => body && body.includes(tag))) return null;

  const assignees = (issue.assignees || []).map((a) => a.login);
  if (assignees.some((a) => a.toLowerCase() === login.toLowerCase())) return null;

  const lines = [];
  if (assignees.length > 0) {
    const who = assignees.map((a) => `@${a}`).join(", ");
    lines.push(
      `Thanks for your interest, @${login}! This issue is already assigned to ${who}.`,
      "",
      "Have a look at the other open " +
        `[good first issues](${repoUrl}/labels/good%20first%20issue) or ` +
        `[help wanted](${repoUrl}/labels/help%20wanted) issues.`,
    );
  } else if (goodFirst) {
    lines.push(
      `Thanks, @${login}! Good first issues are **assigned by a maintainer**: please wait until ` +
        "this issue is assigned to you before opening a pull request. PRs for good first issues " +
        "that aren't assigned to their author are closed.",
      "",
      "Good first issues are meant for people new to the project, one per person at a time. " +
        "If several people ask, the first one who asked usually gets it.",
    );
  } else {
    lines.push(
      `Thanks, @${login}! Help wanted issues are open to anyone: no assignment needed. ` +
        "Share your plan here first (especially for design questions), then open a draft PR " +
        "early so others can see the work is in progress.",
    );
  }
  lines.push("", `See [Picking an issue](${policyUrl(repoUrl)}).`, tag);
  return lines.join("\n");
}

const LINK_PATTERN = /\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)/gi;

/** Issue numbers a PR body says it closes ("Closes #12", "fixes #3", ...). */
function linkedIssues(body) {
  const numbers = new Set();
  for (const match of (body || "").matchAll(LINK_PATTERN)) numbers.add(Number(match[1]));
  return [...numbers].sort((a, b) => a - b);
}

/**
 * A note for a pull request that links good first issues it isn't entitled to, or null.
 * `issues` are the linked issue objects; `mergedCount` is how many PRs the author has merged.
 */
function reviewPullRequest({ pr, issues, mergedCount, repoUrl, previousBodies = [] }) {
  const login = pr.user.login;
  if (pr.user.type === "Bot" || isMaintainer(pr.author_association)) return null;
  const tag = marker("pr", login);
  if (previousBodies.some((body) => body && body.includes(tag))) return null;

  const problems = [];
  for (const issue of issues) {
    if (!labelNames(issue).includes(GOOD_FIRST_ISSUE)) continue;
    const assignees = (issue.assignees || []).map((a) => a.login.toLowerCase());
    if (assignees.length === 0) {
      problems.push(`#${issue.number} is a good first issue that isn't assigned to anyone yet.`);
    } else if (!assignees.includes(login.toLowerCase())) {
      problems.push(`#${issue.number} is a good first issue assigned to someone else.`);
    }
  }
  const linksGoodFirst = issues.some((issue) => labelNames(issue).includes(GOOD_FIRST_ISSUE));
  if (linksGoodFirst && mergedCount >= NEWCOMER_MERGED_PR_LIMIT) {
    problems.push(
      `You already have ${mergedCount} merged PRs here, so please leave good first issues to ` +
        `newcomers and pick a [help wanted](${repoUrl}/labels/help%20wanted) issue instead.`,
    );
  }
  if (problems.length === 0) return null;
  return [
    `Thanks for the PR, @${login}! Before review, a note on our issue policy:`,
    "",
    ...problems.map((p) => `- ${p}`),
    "",
    `A maintainer will decide how to proceed. See [Picking an issue](${policyUrl(repoUrl)}).`,
    tag,
  ].join("\n");
}

module.exports = {
  GOOD_FIRST_ISSUE,
  HELP_WANTED,
  NEWCOMER_MERGED_PR_LIMIT,
  linkedIssues,
  replyToIssueComment,
  reviewPullRequest,
};
