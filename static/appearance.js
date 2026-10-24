/* «Операции → Оформление»: предпросмотр без сохранения. */
(function () {
  var f = document.getElementById("uiform");
  if (!f) return;
  var root = document.documentElement, pick = f.querySelector("input[type=color]");
  function val(n) { var r = f.querySelector("input[name=" + n + "]:checked"); return r ? r.value : ""; }
  function color() {
    var r = f.querySelector("input[name=ui_accent]:checked");
    return r && r.dataset.color === "custom" ? pick.value : (r ? r.dataset.color : "");
  }
  function apply(ev) {
    if (ev && ev.target === pick) { var c = f.querySelector("input[name=ui_accent][value=custom]"); if (c) c.checked = true; }
    root.dataset.theme = val("ui_theme") || root.dataset.theme;
    root.dataset.font = val("ui_font") || root.dataset.font;
    root.dataset.density = val("ui_density") || root.dataset.density;
    root.dataset.radius = val("ui_radius") || root.dataset.radius;
    root.dataset.header = val("ui_header") || root.dataset.header;
    root.dataset.ff = val("ui_ff") || root.dataset.ff;
    root.dataset.skin = val("ui_skin") || root.dataset.skin;
    root.dataset.zebra = f.ui_zebra.checked ? "1" : "0";
    root.style.setProperty("--g", color());
    document.getElementById("customdot").style.background = pick.value;
    if (ev && ev.target === f.ui_title) {
      var t = f.ui_title.value.trim() || "Lumberture";
      document.getElementById("pvtitle").textContent = t;
    }
  }
  f.addEventListener("change", apply);
  f.addEventListener("input", apply);
  f.logo.addEventListener("change", function () {
    var file = f.logo.files[0];
    if (!file) return;
    if (file.size > 1024 * 1024) { alert("Логотип — не больше 1 МБ"); f.logo.value = ""; return; }
    var url = URL.createObjectURL(file), top = document.querySelector(".pv .pvtop"), img = top.querySelector("img.brandlogo");
    if (!img) { img = document.createElement("img"); img.className = "brandlogo"; img.alt = ""; img.style.height = "22px"; top.insertBefore(img, top.firstChild); }
    img.src = url;
  });
})();
