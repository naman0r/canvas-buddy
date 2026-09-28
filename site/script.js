const REPO = "naman0r/canvas-buddy";

document.querySelector("[data-copy]").addEventListener("click", async (event) => {
  await navigator.clipboard.writeText("brew install naman0r/tap/canvas-buddy");
  event.target.textContent = "Copied";
  setTimeout(() => (event.target.textContent = "Copy"), 1500);
});

fetch(`https://api.github.com/repos/${REPO}`)
  .then((response) => (response.ok ? response.json() : null))
  .then((repo) => {
    // A zero count reads as a warning, not social proof; show it once someone has starred.
    if (repo?.stargazers_count) {
      document.querySelector("[data-star-count]").textContent = repo.stargazers_count.toLocaleString();
    }
  })
  .catch(() => {});

document.querySelector("[data-request]").addEventListener("submit", (event) => {
  event.preventDefault();
  const title = new FormData(event.target).get("title").trim();
  const params = new URLSearchParams({ title, labels: "enhancement" });
  window.open(`https://github.com/${REPO}/issues/new?${params}`, "_blank", "noopener");
});
