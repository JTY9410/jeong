async function fetchUnread() {
  const res = await fetch("/api/notifications/unread", {
    credentials: "same-origin",
    headers: { "Accept": "application/json" }
  });
  if (!res.ok) return { items: [] };
  return await res.json();
}

async function markRead(ids) {
  const res = await fetch("/api/notifications/mark-read", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "Accept": "application/json", "X-CSRFToken": window.csrfToken || "" },
    body: JSON.stringify({ ids })
  });
  return res.ok;
}

function showToast(message, type = "info") {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const el = document.createElement("div");
  el.className = `toast toast-${type}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transform = "translateY(8px) scale(.98)"; }, 5200);
  setTimeout(() => { el.remove(); }, 5600);
}

window.showToast = showToast;

function initChart() {
  if (typeof Chart === "undefined") return;
  const el = document.getElementById("chart-data");
  const canvas = document.getElementById("chart-14d");
  if (!el || !canvas) return;

  const data = JSON.parse(el.textContent);
  const jobs = data.jobs || [];
  const interns = data.interns || [];

  const labelsSet = new Set();
  for (const [d] of jobs) labelsSet.add(d);
  for (const [d] of interns) labelsSet.add(d);
  const labels = Array.from(labelsSet).sort();

  const mapCounts = (rows) => {
    const m = new Map(rows.map(([d, c]) => [d, c]));
    return labels.map((d) => m.get(d) || 0);
  };

  new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "공개채용", data: mapCounts(jobs), borderColor: "#0d6efd", backgroundColor: "rgba(13,110,253,0.15)", tension: 0.25 },
        { label: "인턴", data: mapCounts(interns), borderColor: "#198754", backgroundColor: "rgba(25,135,84,0.15)", tension: 0.25 }
      ]
    },
    options: {
      responsive: true,
      plugins: { legend: { position: "bottom" } },
      scales: { y: { beginAtZero: true, ticks: { precision: 0 } } }
    }
  });
}

async function pollNotifications() {
  const unreadEl = document.getElementById("unread-count");
  if (!unreadEl) return;
  let lastSeenIds = new Set();

  setInterval(async () => {
    const data = await fetchUnread();
    const items = data.items || [];
    unreadEl.textContent = String(items.length);

    const newOnes = items.filter((x) => !lastSeenIds.has(x.id));
    if (newOnes.length > 0) {
      const top = newOnes[0];
      showToast(`[${top.company}] ${top.title}`, "info");
    }
    lastSeenIds = new Set(items.map((x) => x.id));
  }, 5000);
}

async function bindRunCrawl() {
  const btn = document.getElementById("btn-run-crawl");
  if (!btn) return;

  let pollTimer = null;
  function stopPoll() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  async function pollStatus() {
    try {
      const res = await fetch("/api/crawl/status", {
        credentials: "same-origin",
        headers: { "Accept": "application/json" }
      });
      const data = await res.json().catch(() => ({}));
      if (data.status === "running") return;
      stopPoll();
      btn.disabled = false;
      if (data.status === "done") {
        btn.textContent = "지금 크롤링 실행";
        showToast("크롤링 완료!", "success");
      } else if (data.status === "error") {
        btn.textContent = "지금 크롤링 실행";
        showToast(data.error || "크롤링 실패", "error");
      } else {
        btn.textContent = "지금 크롤링 실행";
      }
    } catch (_) {}
  }

  btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.textContent = "시작 중...";
    stopPoll();
    try {
      const res = await fetch("/api/crawl/run", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Accept": "application/json",
          "Content-Type": "application/json",
          "X-CSRFToken": window.csrfToken || ""
        },
        body: JSON.stringify({})
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.message || data.error || `HTTP ${res.status}`);
      btn.textContent = "크롤링 중...";
      pollTimer = setInterval(pollStatus, 2000);
    } catch (e) {
      btn.disabled = false;
      btn.textContent = "지금 크롤링 실행";
      showToast(e && e.message ? e.message : "크롤링 실패", "error");
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  // CSRF token for AJAX (Flask-WTF exposes csrf_token() in templates only)
  const meta = document.querySelector('meta[name="csrf-token"]');
  if (meta) window.csrfToken = meta.getAttribute("content");

  initChart();
  pollNotifications();
  bindRunCrawl();

  // Mobile sidebar toggle (project001 style)
  const sidebarToggle = document.getElementById("sidebarToggle");
  const sidebar = document.getElementById("sidebar");
  if (sidebarToggle && sidebar) {
    sidebarToggle.addEventListener("click", () => sidebar.classList.toggle("mobile-open"));
    document.addEventListener("click", (e) => {
      if (!sidebar.contains(e.target) && e.target !== sidebarToggle) {
        sidebar.classList.remove("mobile-open");
      }
    });
  }
});

