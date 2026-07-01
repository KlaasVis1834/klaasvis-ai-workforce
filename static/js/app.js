document.addEventListener("DOMContentLoaded", () => {
    const liveDashboard = document.querySelector(".dashboard-live");
    if (!liveDashboard) {
        return;
    }

    const seconds = Number(liveDashboard.dataset.refreshSeconds || "15");
    window.setInterval(() => window.location.reload(), seconds * 1000);
});
