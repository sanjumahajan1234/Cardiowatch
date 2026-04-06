// CardioWatch shared client-side utilities
(function () {

    // -------------------------------------------------------------------------
    // showToast — non-blocking status notification (bottom-right corner)
    // -------------------------------------------------------------------------
    function showToast(message, type) {
        var el = document.getElementById('global-toast');
        if (!el) {
            el = document.createElement('div');
            el.id = 'global-toast';
            el.style.cssText = [
                'position:fixed', 'right:16px', 'bottom:16px',
                'padding:10px 16px', 'border-radius:8px',
                'box-shadow:0 4px 12px rgba(0,0,0,0.15)',
                'z-index:2000', 'font-size:0.875rem',
                'display:none', 'max-width:320px', 'line-height:1.4',
                'transition:opacity 0.3s'
            ].join(';');
            document.body.appendChild(el);
        }
        el.textContent = message;
        el.style.background = type === 'error' ? '#fee2e2' : '#dcfce7';
        el.style.color      = type === 'error' ? '#991b1b' : '#166534';
        el.style.border     = type === 'error' ? '1px solid #fecaca' : '1px solid #bbf7d0';
        el.style.display    = 'block';
        el.style.opacity    = '1';
        clearTimeout(el._timeout);
        el._timeout = window.setTimeout(function () {
            el.style.opacity = '0';
            window.setTimeout(function () { el.style.display = 'none'; }, 300);
        }, 3500);
    }

    // -------------------------------------------------------------------------
    // Active nav link highlighting — marks the current page's nav link
    // -------------------------------------------------------------------------
    function highlightActiveNav() {
        var path = window.location.pathname;
        document.querySelectorAll('.nav-link').forEach(function (link) {
            var href = link.getAttribute('href');
            var isActive = (href === '/' && path === '/') ||
                           (href !== '/' && path.startsWith(href));
            link.style.color    = isActive ? '#dc2626' : '';
            link.style.fontWeight = isActive ? '600' : '';
        });
    }

    // -------------------------------------------------------------------------
    // Flash message auto-dismiss — fades out after 5 seconds
    // -------------------------------------------------------------------------
    function autoDismissFlash() {
        document.querySelectorAll('.alert').forEach(function (el) {
            window.setTimeout(function () {
                el.style.transition = 'opacity 0.5s';
                el.style.opacity    = '0';
                window.setTimeout(function () { el.remove(); }, 500);
            }, 5000);
        });
    }

    // -------------------------------------------------------------------------
    // classifyBP — JS wrapper around /api/classify-bp
    // Returns a Promise resolving to { category, label, color, systolic, diastolic }
    // -------------------------------------------------------------------------
    function classifyBP(systolic, diastolic) {
        return fetch('/api/classify-bp', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ systolic: systolic, diastolic: diastolic })
        }).then(function (r) { return r.json(); });
    }

    // -------------------------------------------------------------------------
    // Init
    // -------------------------------------------------------------------------
    document.addEventListener('DOMContentLoaded', function () {
        highlightActiveNav();
        autoDismissFlash();
    });

    window.CardioWatchUI = {
        showToast:  showToast,
        classifyBP: classifyBP,
    };

})();
