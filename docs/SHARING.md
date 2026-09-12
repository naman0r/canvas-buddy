# Sharing Canvas Buddy

Research checked September 12, 2026. No posts or messages to communities have been sent. Ranking reflects audience fit, not a promise that moderators will accept a post. Recheck rules and pinned posts on the day you post.

Share the fictional-data preview for UX feedback now. Wait for the authentication work described in [REVIEW.md](REVIEW.md) before inviting other people to connect real Canvas accounts. The preview is source-only; the Homebrew 0.2.0 release does not have the demo command yet.

## Where to share

| Surface | Fit and approach | Rules / evidence |
| --- | --- | --- |
| [r/NEU](https://www.reddit.com/r/NEU/) | Best initial audience fit: a student-made Canvas companion. Ask for a few people to try the fictional demo; disclose that it is independent of Northeastern. | Published rules do not specifically settle project promotion; check current pinned posts and seek moderator guidance if unclear. [Rules](https://www.reddit.com/r/NEU/about/rules/) |
| [Textual Discord](https://github.com/Textualize/textual#join-us-on-discord) | Best engineering feedback fit: keyboard navigation, small-terminal layout, packaging. The official repo supplies the invitation. | The public invite is verified; private channel names and advertising rules were not. Read the server's current rules and use its designated project channel if allowed. |
| [r/SideProject](https://www.reddit.com/r/SideProject/) | Practical early demo and onboarding feedback; a short screen recording plus a specific question. | Its public rules endpoint returned no community-specific rules, which does not establish unrestricted promotion. Review the sidebar/pins and avoid repetitive link drops. [Rules endpoint](https://www.reddit.com/r/SideProject/about/rules.json) |
| [r/Python](https://www.reddit.com/r/Python/) | Smaller technical audience: Textual + SQLite with optional embeddings. | Use the current monthly showcase or relevant daily thread; standalone showcases are disallowed by the current first rule. Older showcase-format rules remain listed, so follow the restrictive current rule. [Rules](https://www.reddit.com/r/Python/about/rules/) |
| [Show HN](https://news.ycombinator.com/showhn.html) | Later, after real feedback and clear launch limitations. Provide a runnable demo and explain the personal problem. | No signup barrier is preferred. Do not solicit votes. HN also prohibits generated or AI-edited text, so write your own submission/comments; do not paste the drafts below. New contributors also face temporary Show HN restrictions. [General guidelines](https://news.ycombinator.com/newsguidelines.html), [current restriction](https://news.ycombinator.com/showlim) |
| [Python Discord](https://www.pythondiscord.com/) | Secondary code-review/showcase possibility. | Advertising requires approval; channel relevance and restrictions on pasted AI answers apply. Seek staff guidance before sharing. [Rules](https://www.pythondiscord.com/pages/rules/) |
| [r/opensource](https://www.reddit.com/r/opensource/) | Conditional, lower priority for this AI-assisted project. | MIT license meets the license condition, but rules broadly reject AI-generated content, limit promotion, and require Promotional flair. Do not paste this draft there; clarify project eligibility first. [Rules](https://www.reddit.com/r/opensource/about/rules/) |
| [r/commandline](https://www.reddit.com/r/commandline/) | **Skip for this launch.** | Current rules prohibit new generative-AI projects and projects under 30 days old, and forbid AI-written posts/titles. Good topical fit does not override those rules. [Rules](https://www.reddit.com/r/commandline/about/rules/) |

Start with a few peers and one relevant community, fix reported friction, then widen the audience. A 30–45 second recording should show the fictional demo, Upcoming, the attendance search, and Browse. No real course screenshots or tokens. Measure useful reports and successful trials, not stars.

## General pitch draft

For channels that permit project promotion and AI-assisted text; adapt to your actual experience. Do not use on HN or communities prohibiting generated writing.

**Canvas Buddy: an open-source terminal companion for Canvas classes**

I built Canvas Buddy to make it easier to find deadlines, announcements, grades, and syllabus policies without jumping between Canvas tabs. It caches selected classes locally and can answer questions with Canvas source links using Codex/OpenCode or a local Ollama model. Search and browsing work without a chat model.

I'm looking for feedback on an early fictional-data demo: can you find an upcoming assignment and the attendance policy without reading a manual? The app is Python + Textual + SQLite, MIT licensed, and makes read-only Canvas requests. The current real-account connection is for personal testing; OAuth is still needed before broader onboarding. Cloud model options send selected course text to your chosen provider.

Source: https://github.com/naman0r/canvas-buddy

To try the current preview with Python 3.11+ and uv installed:

```sh
git clone https://github.com/naman0r/canvas-buddy.git
cd canvas-buddy
uv run canvas-buddy demo
```

## Short Discord draft

I built Canvas Buddy, a small Textual TUI for finding Canvas deadlines, grades, and course policies. There's an offline fictional-data demo on the source branch—no token or model needed—and I'd appreciate feedback on the keyboard flow and first-use experience: https://github.com/naman0r/canvas-buddy. Real-account onboarding is still a personal-testing preview pending OAuth.

## Notes for writing your own restricted-channel post

Use your real story: the Canvas lookup that frustrated you, why a terminal helped, one design tradeoff you made, and what feedback you want. Explain the local/cloud distinction, partial Canvas coverage, authentication limitation, and AI-assisted development candidly. Do not claim a security audit, universal compatibility, official university affiliation, guaranteed subscription coverage, or access to everything in Canvas.
