// CardioWatch shared client helpers
(function () {
    function showToast(message, type) {
        var el = document.getElementById("global-toast");
        if (!el) {
            el = document.createElement("div");
            el.id = "global-toast";
            el.style.position = "fixed";
            el.style.right = "16px";
            el.style.bottom = "16px";
            el.style.padding = "10px 14px";
            el.style.borderRadius = "8px";
            el.style.boxShadow = "0 4px 10px rgba(0,0,0,0.15)";
            el.style.zIndex = "2000";
            el.style.fontSize = "0.875rem";
            el.style.display = "none";
            document.body.appendChild(el);
        }

        el.textContent = message;
        el.style.background = type === "error" ? "#fee2e2" : "#dcfce7";
        el.style.color = type === "error" ? "#991b1b" : "#166534";
        el.style.border = type === "error" ? "1px solid #fecaca" : "1px solid #bbf7d0";
        el.style.display = "block";

        window.setTimeout(function () {
            el.style.display = "none";
        }, 2800);
    }

    window.CardioWatchUI = {
        showToast: showToast
    };
})();
