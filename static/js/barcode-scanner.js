/*
 * Scan de code-barres — module partagé (caisse de vente, formulaire produit).
 *
 * Deux sources de scan, indépendantes :
 *  1. Lecteur USB / Bluetooth : il se comporte comme un clavier qui « tape » le
 *     code très vite puis appuie sur Entrée. Voir listenHardware().
 *  2. Caméra du téléphone / de la tablette, via l'API native BarcodeDetector
 *     (Chrome Android, Chrome sur Mac...). Voir openCamera(). Elle exige une
 *     page en HTTPS (ou localhost) et n'existe pas dans tous les navigateurs :
 *     cameraSupported() permet de masquer le bouton là où elle est absente.
 *
 * Aucune dépendance externe : l'application doit rester utilisable hors-ligne.
 */
(function () {
    "use strict";

    var FORMATS = ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39", "code_93", "itf", "codabar", "qr_code"];
    var SAME_CODE_GAP_MS = 700;   // code absent du champ plus longtemps que ça = nouveau scan
    var DETECT_EVERY_MS = 120;

    // ------------------------------------------------------------------ retour sonore
    var audioCtx = null;

    // À appeler depuis un geste utilisateur (clic, touche) : les navigateurs
    // refusent de démarrer un son sinon.
    function ensureAudio() {
        if (audioCtx) return;
        var Ctx = window.AudioContext || window.webkitAudioContext;
        if (Ctx) {
            try { audioCtx = new Ctx(); } catch (e) { audioCtx = null; }
        }
    }

    function tone(frequency, start, duration) {
        var oscillator = audioCtx.createOscillator();
        var gain = audioCtx.createGain();
        oscillator.frequency.value = frequency;
        gain.gain.value = 0.15;
        oscillator.connect(gain);
        gain.connect(audioCtx.destination);
        oscillator.start(audioCtx.currentTime + start);
        oscillator.stop(audioCtx.currentTime + start + duration);
    }

    // "ok" : un bip aigu. "error" : deux bips graves — reconnaissable sans regarder l'écran.
    function beep(kind) {
        if (navigator.vibrate) navigator.vibrate(kind === "error" ? [60, 40, 60] : 40);
        if (!audioCtx) return;
        try {
            if (audioCtx.state === "suspended") audioCtx.resume();
            if (kind === "error") { tone(220, 0, 0.12); tone(220, 0.18, 0.12); }
            else { tone(1100, 0, 0.09); }
        } catch (e) { /* le son est un confort, jamais bloquant */ }
    }

    // ------------------------------------------------------------------ lecteur USB / Bluetooth
    /*
     * Capte les codes « tapés » par un lecteur quand aucun champ n'a le focus.
     * Un lecteur envoie ses caractères à quelques millisecondes d'intervalle ;
     * un humain, jamais aussi vite : un intervalle > maxGapMs remet le tampon à
     * zéro, donc une frappe normale n'est jamais prise pour un scan. Quand un
     * champ de saisie a le focus, on ne fait rien : c'est ce champ qui reçoit
     * les caractères (à lui de traiter Entrée).
     */
    function listenHardware(options) {
        var minLength = options.minLength || 4;
        var maxGapMs = options.maxGapMs || 60;
        var buffer = "";
        var last = 0;

        document.addEventListener("keydown", function (event) {
            ensureAudio();
            if (event.ctrlKey || event.altKey || event.metaKey) return;
            var target = event.target;
            var typingInAField = target && (
                target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)
            );
            if (typingInAField || document.querySelector(".modal.show")) { buffer = ""; return; }

            if (event.key === "Enter") {
                var code = buffer;
                buffer = "";
                if (code.length >= minLength) {
                    event.preventDefault();
                    options.onCode(code);
                }
                return;
            }
            if (event.key.length !== 1) return;   // Shift, Tab, flèches...
            var now = performance.now();
            if (now - last > maxGapMs) buffer = "";
            buffer += event.key;
            last = now;
        });
    }

    // ------------------------------------------------------------------ caméra
    function cameraSupported() {
        return !!(
            window.isSecureContext !== false &&
            navigator.mediaDevices && navigator.mediaDevices.getUserMedia &&
            "BarcodeDetector" in window
        );
    }

    function escapeHtml(text) {
        var div = document.createElement("div");
        div.textContent = text == null ? "" : text;
        return div.innerHTML;
    }

    var dialog = null;   // {el, modal, video, status}

    function buildDialog(labels) {
        var el = document.createElement("div");
        el.className = "modal fade";
        el.tabIndex = -1;
        el.setAttribute("aria-hidden", "true");
        el.innerHTML =
            '<div class="modal-dialog modal-dialog-centered"><div class="modal-content">' +
            '<div class="modal-header"><h5 class="modal-title">' + escapeHtml(labels.title) + '</h5>' +
            '<button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="' + escapeHtml(labels.close) + '"></button></div>' +
            '<div class="modal-body p-0">' +
            '<div style="position:relative;background:#000;">' +
            '<video playsinline muted autoplay style="width:100%;max-height:60vh;display:block;object-fit:cover;"></video>' +
            '<div style="position:absolute;left:12%;right:12%;top:50%;height:0;border-top:2px solid rgba(255,64,64,.85);pointer-events:none;"></div>' +
            '</div>' +
            '<div class="p-3 text-center" data-role="status" aria-live="polite"></div>' +
            '</div></div></div>';
        document.body.appendChild(el);
        return {
            el: el,
            modal: new bootstrap.Modal(el),
            video: el.querySelector("video"),
            status: el.querySelector('[data-role="status"]'),
        };
    }

    function setStatus(text, tone) {
        dialog.status.className = "p-3 text-center " + (tone === "ok" ? "text-success fw-semibold" : tone === "error" ? "text-danger" : "text-muted");
        dialog.status.textContent = text;
    }

    /*
     * Ouvre la caméra dans une fenêtre. options :
     *  - labels {title, close, hint, unsupported, denied, noCamera, insecure}
     *  - continuous : true = la fenêtre reste ouverte et enchaîne les scans (caisse) ;
     *                 false = se ferme au premier code lu (remplir un champ).
     *  - onCode(code) : appelé à chaque code lu ; peut renvoyer (ou promettre)
     *                   {ok: bool, message: "..."} affiché dans la fenêtre.
     */
    function openCamera(options) {
        var labels = options.labels || {};
        ensureAudio();
        if (!cameraSupported()) {
            window.alert(window.isSecureContext === false ? labels.insecure : labels.unsupported);
            return;
        }
        if (!dialog) dialog = buildDialog(labels);

        var stream = null;
        var running = false;
        var busy = false;
        var timer = null;
        var hintTimer = null;   // retour à la consigne ; un seul à la fois, sinon un ancien efface le message suivant
        var seen = { code: null, at: 0 };

        function stop() {
            running = false;
            clearTimeout(timer);
            clearTimeout(hintTimer);
            if (stream) stream.getTracks().forEach(function (track) { track.stop(); });
            stream = null;
            dialog.video.srcObject = null;
        }

        function handle(code) {
            var now = performance.now();
            var sameAsBefore = seen.code === code && now - seen.at < SAME_CODE_GAP_MS;
            seen = { code: code, at: now };
            if (sameAsBefore) return Promise.resolve();   // le même code, toujours devant la caméra

            busy = true;
            // Promise.resolve().then(...) : une exception de onCode est captée
            // (sinon `busy` resterait à true et la caméra ne lirait plus rien).
            return Promise.resolve().then(function () { return options.onCode(code); }).then(function (result) {
                result = result || { ok: true };
                beep(result.ok === false ? "error" : "ok");
                setStatus(result.message || "", result.ok === false ? "error" : "ok");
                if (!options.continuous && result.ok !== false) {
                    dialog.modal.hide();
                } else {
                    clearTimeout(hintTimer);
                    hintTimer = setTimeout(function () { if (running) setStatus(labels.hint, "hint"); }, 2200);
                }
            }).catch(function () {
                beep("error");
                setStatus(labels.error || "", "error");
            }).then(function () { busy = false; });
        }

        function tick(detector) {
            if (!running) return;
            if (busy || dialog.video.readyState < 2) { timer = setTimeout(function () { tick(detector); }, DETECT_EVERY_MS); return; }
            detector.detect(dialog.video).then(function (found) {
                if (found.length && found[0].rawValue) return handle(found[0].rawValue.trim());
            }).catch(function () { /* image illisible : on réessaie au prochain passage */ }).then(function () {
                timer = setTimeout(function () { tick(detector); }, DETECT_EVERY_MS);
            });
        }

        function start() {
            setStatus(labels.hint, "hint");
            var supported = BarcodeDetector.getSupportedFormats ? BarcodeDetector.getSupportedFormats() : Promise.resolve(FORMATS);
            Promise.all([
                supported,
                navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false }),
            ]).then(function (values) {
                var formats = FORMATS.filter(function (f) { return values[0].indexOf(f) !== -1; });
                stream = values[1];
                dialog.video.srcObject = stream;
                running = true;
                return dialog.video.play().then(function () {
                    tick(new BarcodeDetector({ formats: formats.length ? formats : undefined }));
                });
            }).catch(function (error) {
                var name = error && error.name;
                setStatus(
                    name === "NotAllowedError" || name === "SecurityError" ? labels.denied
                        : name === "NotFoundError" || name === "OverconstrainedError" ? labels.noCamera
                            : labels.unsupported,
                    "error"
                );
            });
        }

        // Les écouteurs sont recréés à chaque ouverture (options différentes selon la page).
        var onShown = function () { start(); };
        var onHidden = function () {
            stop();
            dialog.el.removeEventListener("shown.bs.modal", onShown);
            dialog.el.removeEventListener("hidden.bs.modal", onHidden);
        };
        dialog.el.addEventListener("shown.bs.modal", onShown);
        dialog.el.addEventListener("hidden.bs.modal", onHidden);
        dialog.el.querySelector(".modal-title").textContent = labels.title;
        dialog.modal.show();
    }

    window.BarcodeScanner = {
        cameraSupported: cameraSupported,
        openCamera: openCamera,
        listenHardware: listenHardware,
        beep: beep,
        ensureAudio: ensureAudio,
    };
})();
