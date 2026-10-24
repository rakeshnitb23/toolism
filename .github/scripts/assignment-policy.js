// Issue policy, enforced by .github/workflows/assignment-policy.yml.
// The rules are written for contributors in CONTRIBUTING.md ("Picking an issue"): no assignment
// needed, the first ready PR is reviewed first, and good first issues are for people with fewer
// than NEWCOMER_MERGED_PR_LIMIT merged PRs. This module only decides *what to say*; the workflow
// does the GitHub API calls. Tested with `node --test .github/scripts/assignment-policy.test.js`.

const GOOD_FIRST_ISSUE = "good first issue";
const HELP_WANTED = "help wanted";
const MAINTAINER_ASSOCIATIONS = new Set(["OWNER", "MEMBER", "COLLABORATOR"]);
// Contributors with this many merged PRs are past "first issue" territory.
const NEWCOMER_MERGED_PR_LIMIT = 5;

const marker = (kind, login) => `<!-- assignment-policy:${kind}:${login.toLowerCase()} -->`;
const policyUrl = (repoUrl) => `${repoUrl}/blob/master/CONTRIBUTING.md#picking-an-issue`;
const same = (a, b) => a.toLowerCase() === b.toLowerCase();

function labelNames(issue) {
  return (issue.labels || []).map((label) => (typeof label === "string" ? label : label.name));
}

function isMaintainer(association) {
  return MAINTAINER_ASSOCIATIONS.has(association);
}

const LINK_PATTERN = /\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)/gi;

/** Issue numbers a PR body says it closes ("Closes #12", "fixes #3", ...). */
function linkedIssues(body) {
  const numbers = new Set();
  for (const match of (body || "").matchAll(LINK_PATTERN)) numbers.add(Number(match[1]));
  return [...numbers].sort((a, b) => a - b);
}

/** Open PRs that link `issueNumber`, oldest first. `openPrs` are pull request payload objects. */
function prsForIssue(issueNumber, openPrs) {
  return openPrs
    .filter((pr) => linkedIssues(pr.body).includes(issueNumber))
    .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)) || a.number - b.number);
}

const describePrs = (prs) => prs.map((pr) => `#${pr.number} by @${pr.user.login}`).join(", ");

/**
 * Reply to a comment on an issue, or return null to stay quiet. `openPrs` are the repository's
 * open pull requests; `mergedCount` is how many PRs the commenter has merged here;
 * `previousBodies` are the issue's existing comments, so each person gets at most one reply.
 */
function replyToIssueComment({
  issue,
  comment,
  repoUrl,
  openPrs = [],
  mergedCount = 0,
  previousBodies = [],
}) {
  const login = comment.user.login;
  if (comment.user.type === "Bot" || isMaintainer(comment.author_association)) return null;
  if (issue.pull_request || issue.state !== "open") return null;
  const labels = labelNames(issue);
  const goodFirst = labels.includes(GOOD_FIRST_ISSUE);
  if (!goodFirst && !labels.includes(HELP_WANTED)) return null;

  const tag = marker("claim", login);
  if (previousBodies.some((body) => body && body.includes(tag))) return null;

  const theirs = prsForIssue(issue.number, openPrs).filter((pr) => !same(pr.user.login, login));
  const lines = [];
  if (goodFirst && mergedCount >= NEWCOMER_MERGED_PR_LIMIT) {
    lines.push(
      `Thanks, @${login}! You already have ${mergedCount} merged PRs here, so please leave good ` +
        `first issues to newcomers and pick a [help wanted](${repoUrl}/labels/help%20wanted) ` +
        "issue instead.",
    );
  } else if (theirs.length > 0) {
    lines.push(
      `Thanks, @${login}! This issue already has an open PR: ${describePrs(theirs)}. ` +
        "The first PR that's ready is reviewed first, so you may want to pick another " +
        `[good first issue](${repoUrl}/labels/good%20first%20issue) or ` +
        `[help wanted](${repoUrl}/labels/help%20wanted) issue. Reviewing the open PR is ` +
        "welcome too.",
    );
  } else {
    lines.push(
      `Thanks, @${login}! **No need to wait for assignment: go ahead and open a pull request.** ` +
        `Put \`Closes #${issue.number}\` in its description.`,
      "",
      "If several people open PRs for the same issue, the first one that's ready (CI green, " +
        "following CONTRIBUTING.md) is reviewed first, so opening a draft PR early helps.",
    );
    if (goodFirst) {
      lines.push(
        "",
        `Good first issues are for people with fewer than ${NEWCOMER_MERGED_PR_LIMIT} merged ` +
          "PRs here.",
      );
    }
  }
  lines.push("", `See [Picking an issue](${policyUrl(repoUrl)}).`, tag);
  return lines.join("\n");
}

/**
 * A note for a pull request, or null. `issues` are the issue objects it links; `openPrs` are the
 * repository's open PRs (including this one); `mergedCount` is how many PRs the author has merged.
 */
function reviewPullRequest({
  pr,
  issues,
  openPrs = [],
  mergedCount,
  repoUrl,
  previousBodies = [],
}) {
  const login = pr.user.login;
  if (pr.user.type === "Bot" || isMaintainer(pr.author_association)) return null;
  const tag = marker("pr", login);
  if (previousBodies.some((body) => body && body.includes(tag))) return null;

  const problems = [];
  for (const issue of issues) {
    const earlier = prsForIssue(issue.number, openPrs).filter(
      (other) =>
        other.number !== pr.number &&
        String(other.created_at).localeCompare(String(pr.created_at)) <= 0,
    );
    if (earlier.length > 0) {
      problems.push(
        `#${issue.number} already has an earlier open PR: ${describePrs(earlier)}. ` +
          "The first PR that's ready is reviewed first.",
      );
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
    `Thanks for the PR, @${login}! A note before review:`,
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
  prsForIssue,
  replyToIssueComment,
  reviewPullRequest,
};
