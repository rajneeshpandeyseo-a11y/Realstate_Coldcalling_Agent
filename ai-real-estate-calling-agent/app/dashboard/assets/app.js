/* AI Real Estate Calling Agent - lightweight static dashboard SPA.
   Serves from the SAME FastAPI process; talks to the existing /api/v1 JSON
   endpoints directly. Requires the X-API-Key header (stored in sessionStorage). */

(function () {
  "use strict";

  var API_KEY_KEY = "admin_api_key";
  var API = "";
  var statusJson = null;

  // ------------------------------------------------------------- helpers

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function esc(s) {
    if (s === null || s === undefined) return "";
    return String(s)
      .replace(/[&<>"']/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
      });
  }

  function fmtMoney(n) {
    n = Number(n || 0);
    return n.toLocaleString("en-IN", { maximumFractionDigits: 4 });
  }

  function fmtDate(iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    return isNaN(d) ? "—" : d.toLocaleString();
  }

  function fmtDur(sec) {
    if (sec === null || sec === undefined) return "—";
    sec = Math.round(sec);
    var m = Math.floor(sec / 60), s = sec % 60;
    return m > 0 ? m + "m " + s + "s" : s + "s";
  }

  function badge(status) {
    if (!status) return '<span class="badge b-gray">—</span>';
    var map = {
      "NEW": "b-blue", "CONTACTED": "b-yellow", "QUALIFIED": "b-green",
      "COMPLETED": "b-green", "IN_PROGRESS": "b-yellow", "ANSWERED": "b-yellow",
      "RINGING": "b-yellow", "INITIATED": "b-blue", "QUEUED": "b-gray",
      "NO_ANSWER": "b-red", "NO_RESPONSE": "b-red", "BUSY": "b-red", "FAILED": "b-red",
      "CANCELLED": "b-red", "DO_NOT_CALL": "b-red", "CALLBACK_REQUESTED": "b-yellow",
      "REQUESTED": "b-yellow", "CONFIRMED": "b-green", "RESCHEDULED": "b-yellow",
      "CANCELLED": "b-red",
    };
    var cls = map[String(status).toUpperCase()] || "b-gray";
    return '<span class="badge ' + cls + '">' + esc(status) + "</span>";
  }

  function setKey(k) {
    try { sessionStorage.setItem(API_KEY_KEY, k); } catch (e) {}
  }
  function getKey() {
    try { return sessionStorage.getItem(API_KEY_KEY) || ""; } catch (e) { return ""; }
  }

  function api(path, opts) {
    opts = opts || {};
    var headers = Object.assign({}, opts.headers || {});
    if (getKey()) headers["X-API-Key"] = getKey();
    if (opts.body !== undefined && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
    var full = API + path;
    return fetch(full, {
      method: opts.method || "GET",
      headers: headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    }).then(function (resp) {
      return resp.json().then(function (data) {
        if (!resp.ok) {
          var msg = (data && data.detail) ? data.detail : ("HTTP " + resp.status);
          var err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
          err.status = resp.status;
          throw err;
        }
        return data;
      });
    });
  }

  // ------------------------------------------------------------- router

  var routes = {
    dashboard: renderDashboard,
    leads: renderLeads,
    calls: renderCalls,
    callDetail: renderCallDetail,
    meetings: renderMeetings,
    followups: renderFollowups,
    settings: renderSettings,
    testcall: renderTestCall,
  };

  var pageTitles = {
    dashboard: "Dashboard",
    leads: "Leads",
    calls: "Calls",
    callDetail: "Call Detail",
    meetings: "Meetings / Site Visits",
    followups: "Follow-ups",
    settings: "Settings",
    testcall: "Test Call Control",
  };

  function parseHash() {
    var raw = location.hash.replace(/^#\/?/, "");
    var parts = raw.split("/").filter(Boolean);
    var name = parts[0] || "dashboard";
    return { name: name, args: parts.slice(1) };
  }

  function navigate() {
    var info = parseHash();
    var fn = routes[info.name] || routes.dashboard;
    var view = $("#view");
    view.innerHTML = '<div class="loading">Loading…</div>';
    $("#pageTitle").textContent = pageTitles[info.name] || "Dashboard";
    $$(".sidebar nav a").forEach(function (a) {
      a.classList.toggle("active", a.dataset.route === info.name);
    });
    Promise.resolve(fn(view, info.args)).catch(function (e) {
      view.innerHTML =
        '<div class="panel"><div class="panel-body"><div class="empty">Error: ' +
        esc(e.message) +
        "</div></div></div>";
    });
  }

  window.addEventListener("hashchange", navigate);

  // ------------------------------------------------------------- shared pieces

  function loadStatus() {
    return fetch(API + "/api/v1/meta/status").then(function (r) { return r.json(); });
  }

  function kpiRow(stat) {
    var t = stat.totals;
    return (
      '<div class="grid">' +
      kpi("Total Leads", t.leads) +
      kpi("Total Calls", t.calls) +
      kpi("Completed Calls", t.completed_calls) +
      kpi("Failed Calls", t.failed_calls) +
      kpi("Qualified Leads", t.qualified_leads) +
      kpi("Site Visits", t.site_visits) +
      kpi("Follow-ups", t.follow_ups) +
      kpi("Est. Cost (₹)", fmtMoney(stat.cost.estimated_total)) +
      "</div>"
    );
  }
  function kpi(label, value) {
    return (
      '<div class="kpi"><div class="k-label">' + esc(label) + "</div>" +
      '<div class="k-value">' + esc(value === undefined || value === null ? "—" : value) + "</div></div>"
    );
  }

  function confirm(text, details, title) {
    return new Promise(function (resolve) {
      var dlg = $("#confirmDialog");
      $("#dialogTitle").textContent = title || "Confirm";
      $("#dialogText").textContent = text;
      $("#dialogDetails").innerHTML = details || "";
      $("#dialogOk").onclick = function () { dlg.close(); resolve(true); };
      $("#dialogCancel").onclick = function () { dlg.close(); resolve(false); };
      dlg.showModal();
    });
  }

  // ------------------------------------------------------------- dashboard

  function renderDashboard(view) {
    var container = document.createElement("div");
    view.innerHTML = "";
    view.appendChild(container);

    var statPromise = api("/api/v1/meta/stats");
    var recentPromise = api("/api/v1/calls?page_size=8");
    var leadPromise = api("/api/v1/leads?page_size=6");

    Promise.all([statPromise, recentPromise, leadPromise]).then(function (res) {
      container.innerHTML =
        kpiRow(res[0]) +
        '<div class="panel"><div class="panel-head"><h2>Recent Calls</h2><a href="#/calls">View all</a></div>' +
        '<div class="panel-body">' +
        (res[1].items.length ? callsTable(res[1].items) : '<div class="empty">No calls yet.</div>') +
        "</div></div>" +
        '<div class="panel"><div class="panel-head"><h2>Recent Leads</h2><a href="#/leads">View all</a></div>' +
        '<div class="panel-body">' +
        (res[2].items.length ? leadsTable(res[2].items) : '<div class="empty">No leads yet.</div>') +
        "</div></div>";
    }).catch(function (e) {
      container.innerHTML = errBox(e);
    });
  }

  // ------------------------------------------------------------- leads

  function renderLeads(view) {
    var q = buildQuery({ page_size: 100 });
    api("/api/v1/leads?" + q).then(function (d) {
      view.innerHTML =
        '<div class="panel"><div class="panel-head"><h2>Leads</h2>' +
        '<a href="#/leads" class="btn" onclick="event.preventDefault()">New lead (form)</a></div>' +
        '<div class="panel-body">' +
        (d.items.length ? leadsTable(d.items) : '<div class="empty">No leads yet.</div>') +
        "</div></div>";
    }).catch(function (e) { view.innerHTML = errBox(e); });
  }

  function leadsTable(items) {
    var rows = items.map(function (l) {
      return (
        "<tr>" +
        "<td>" + esc(l.name || "—") + "</td>" +
        "<td>" + esc(l.phone) + "</td>" +
        "<td>" + badge(l.status) + "</td>" +
        "<td>" + badge(l.score || "—") + "</td>" +
        "<td>" + esc(l.city || "—") + "</td>" +
        "<td>" + esc(l.do_not_call ? "Yes" : "No") + "</td>" +
        "<td>" + fmtDate(l.created_at) + "</td>" +
        "</tr>"
      );
    }).join("");
    return (
      "<table><thead><tr><th>Name</th><th>Phone</th><th>Status</th><th>Score</th><th>City</th><th>DNC</th><th>Created</th></tr></thead><tbody>" +
      rows + "</tbody></table>"
    );
  }

  // ------------------------------------------------------------- calls

  function renderCalls(view) {
    var q = buildQuery({ page_size: 100 });
    api("/api/v1/calls?" + q).then(function (d) {
      view.innerHTML =
        '<div class="panel"><div class="panel-head"><h2>Calls</h2><span class="auth-note">' + d.total + " total</span></div>" +
        '<div class="panel-body">' +
        (d.items.length ? callsTable(d.items) : '<div class="empty">No calls yet.</div>') +
        "</div></div>";
    }).catch(function (e) { view.innerHTML = errBox(e); });
  }

  function callsTable(items) {
    var rows = items.map(function (c) {
      return (
        "<tr>" +
        "<td><a href=\"#/calls/" + c.id + "\">" + esc(c.id) + "</a></td>" +
        "<td>" + esc(c.phone_number || "—") + "</td>" +
        "<td>" + badge(c.status) + "</td>" +
        "<td>" + esc(c.provider) + "</td>" +
        "<td>" + fmtDur(c.duration_seconds) + "</td>" +
        "<td>" + fmtDate(c.created_at) + "</td>" +
        "<td>" + (c.recording_url ? "Yes" : (c.recording_enabled ? "Pending" : "—")) + "</td>" +
        "</tr>"
      );
    }).join("");
    return (
      "<table><thead><tr><th>ID</th><th>Phone</th><th>Status</th><th>Provider</th><th>Duration</th><th>Started</th><th>Recording</th></tr></thead><tbody>" +
      rows + "</tbody></table>"
    );
  }

  // ------------------------------------------------------------- call detail

  function renderCallDetail(view, args) {
    var callId = args[0];
    if (!callId) { view.innerHTML = errBox(new Error("no call id")); return; }

    var callP = api("/api/v1/calls/" + callId);
    var turnP = api("/api/v1/calls/" + callId + "/transcript.json").catch(function () { return null; });
    var eventsP = api("/api/v1/calls/" + callId + "/events").catch(function () { return null; });
    var recP = api("/api/v1/calls/" + callId + "/recording").catch(function () { return null; });
    var reqP = api("/api/v1/calls/" + callId + "/requirements").catch(function () { return null; });
    var costP = api("/api/v1/calls/" + callId + "/cost").catch(function () { return null; });

    Promise.all([callP, turnP, eventsP, recP, reqP, costP]).then(function (r) {
      var call = r[0];
      var transcript = r[1];
      var events = r[2];
      var rec = r[3];
      var reqs = r[4];
      var cost = r[5];

      var transcriptHtml;
      if (transcript && transcript.messages && transcript.messages.length) {
        transcriptHtml =
          '<div class="transcript">' +
          transcript.messages.map(function (m) {
            return (
              '<div class="turn"><div class="who">' + esc(m.speaker) + "</div>" +
              '<div class="body"><div class="meta">' + fmtDate(m.timestamp) +
              (m.state ? ' · ' + esc(m.state) : "") + '</div>' +
              esc(m.text) + "</div></div>"
            );
          }).join("") +
          "</div>";
      } else {
        transcriptHtml = '<div class="empty">No transcript messages stored for this call.</div>';
      }

      var eventsHtml = events && events.length
        ? "<table><thead><tr><th>Time</th><th>Type</th><th>Data</th></tr></thead><tbody>" +
          events.map(function (e) {
            return "<tr><td>" + fmtDate(e.occurred_at) + "</td><td>" + esc(e.event_type) +
              "</td><td>" + esc(typeof e.data === "string" ? e.data : JSON.stringify(e.data || "")) + "</td></tr>";
          }).join("") +
          "</tbody></table>"
        : '<div class="empty">No lifecycle events.</div>';

      var recHtml = rec
        ? (rec.status === "available"
            ? 'Recording: <a href="' + esc(rec.recording_url) + '" target="_blank">' + esc(rec.recording_url) + "</a>"
            : "Status: " + esc(rec.status))
        : "No recording info";

      var reqRows = reqs && reqs.requirements ? reqs.requirements : [];
      var reqHtml =
        '<div class="grid">' +
        (reqRows.length
          ? reqRows.map(function (r) {
              return (
                kpi("Property Type", r.property_type || "—") +
                kpi("BHK", r.bhk || "—") +
                kpi("Location", r.location || "—") +
                kpi("City", r.city || "—") +
                kpi("Budget", (r.budget_raw || ("₹" + (r.budget_min || "") + " - ₹" + (r.budget_max || ""))) || "—") +
                kpi("Purpose", r.purpose || "—") +
                kpi("Timeline", r.timeline || "—") +
                kpi("Lead Score", r.lead_score || "—")
              );
            }).join("")
          : kpi("Requirements captured", "None yet"))
        + "</div>";

      view.innerHTML =
        '<div class="panel"><div class="panel-head"><h2>Call ' + esc(call.id) + "</h2>" +
        badge(call.status) + "</div><div class="panel-body">" +
        '<div class="form-grid">' +
        field("Phone", call.phone_number) +
        field("Provider", call.provider) +
        field("Duration", fmtDur(call.duration_seconds)) +
        field("Initiated", fmtDate(call.initiated_at)) +
        field("Answered", fmtDate(call.answered_at)) +
        field("Ended", fmtDate(call.ended_at)) +
        field("Failure", call.failure_reason || "—") +
        '</div></div></div>' +

        '<div class="panel"><div class="panel-head"><h2>Extracted Requirements</h2></div>' +
        '<div class="panel-body">' + reqHtml + "</div></div>" +

        '<div class="panel"><div class="panel-head"><h2>Transcript</h2>' +
        '<span><a href="/api/v1/calls/' + call.id + '/transcript.txt" target="_blank">.txt</a> · ' +
        '<a href="/api/v1/calls/' + call.id + '/transcript.json" target="_blank">.json</a></span></div>' +
        '<div class="panel-body">' + transcriptHtml + "</div></div>" +

        '<div class="panel"><div class="panel-head"><h2>Recording & Cost</h2></div>' +
        '<div class="panel-body">' +
        '<p>' + recHtml + "</p>" +
        (cost
          ? "<p><strong>Est. cost:</strong> ₹" + fmtMoney(cost.total || 0) +
            " (" + esc(cost.components ? JSON.stringify(cost.components) : "") + ")</p>"
          : "") +
        "</div></div>" +

        '<div class="panel"><div class="panel-head"><h2>Lifecycle Events</h2></div>' +
        '<div class="panel-body">' + eventsHtml + "</div></div>";
    }).catch(function (e) { view.innerHTML = errBox(e); });
  }

  function field(label, value) {
    return '<div><label>' + esc(label) + "</label><input readonly value=\"" + esc(value || "") + "\" /></div>";
  }

  // ------------------------------------------------------------- meetings

  function renderMeetings(view) {
    var q = buildQuery({ page_size: 100 });
    api("/api/v1/site-visits?" + q).then(function (d) {
      var items = d.items || [];
      var rows = items.map(function (v) {
        return (
          "<tr>" +
          "<td>" + esc(v.id) + "</td>" +
          "<td>" + esc(v.lead_id) + "</td>" +
          "<td>" + esc(v.preferred_date || "—") + "</td>" +
          "<td>" + esc(v.preferred_time || "—") + "</td>" +
          "<td>" + esc(v.location || "—") + "</td>" +
          "<td>" + badge(v.status) + "</td>" +
          "<td>" + esc(v.confirmed_by_customer ? "Yes" : "No") + "</td>" +
          "</tr>"
        );
      }).join("");
      view.innerHTML =
        '<div class="panel"><div class="panel-head"><h2>Site Visits / Meetings</h2></div>' +
        '<div class="panel-body">' +
        (items.length
          ? "<table><thead><tr><th>ID</th><th>Lead</th><th>Date</th><th>Time</th><th>Location</th><th>Status</th><th>Confirmed</th></tr></thead><tbody>" + rows + "</tbody></table>"
          : '<div class="empty">No site visits booked yet.</div>') +
        "</div></div>";
    }).catch(function (e) { view.innerHTML = errBox(e); });
  }

  // ------------------------------------------------------------- follow-ups

  function renderFollowups(view) {
    var q = buildQuery({ page_size: 100 });
    api("/api/v1/follow-ups?" + q).then(function (d) {
      var items = d.items || [];
      var rows = items.map(function (f) {
        return (
          "<tr>" +
          "<td>" + esc(f.id) + "</td>" +
          "<td>" + esc(f.lead_id) + "</td>" +
          "<td>" + esc(f.reason || "—") + "</td>" +
          "<td>" + fmtDate(f.scheduled_for) + "</td>" +
          "<td>" + badge(f.status) + "</td>" +
          "<td>" + esc(f.attempts) + "/" + esc(f.max_attempts) + "</td>" +
          "</tr>"
        );
      }).join("");
      view.innerHTML =
        '<div class="panel"><div class="panel-head"><h2>Follow-ups</h2></div>' +
        '<div class="panel-body">' +
        (items.length
          ? "<table><thead><tr><th>ID</th><th>Lead</th><th>Reason</th><th>Scheduled</th><th>Status</th><th>Attempts</th></tr></thead><tbody>" + rows + "</tbody></table>"
          : '<div class="empty">No follow-ups scheduled.</div>') +
        "</div></div>";
    }).catch(function (e) { view.innerHTML = errBox(e); });
  }

  // ------------------------------------------------------------- settings

  function renderSettings(view) {
    Promise.all([loadStatus(), api("/api/v1/meta/readiness")]).then(function (r) {
      var st = r[0];
      var read = r[1];
      var p = st.providers;
      view.innerHTML =
        '<div class="panel"><div class="panel-head"><h2>Admin API Authentication</h2></div><div class="panel-body">' +
        '<div class="form-grid">' +
        '<div><label>X-API-Key</label><input id="adminApiKey" type="password" autocomplete="off" placeholder="Enter admin API key" value="' + esc(getKey()) + '" /></div>' +
        '</div>' +
        '<div class="form-actions"><button class="btn" id="saveApiKey">Save API Key</button><button class="btn" id="clearApiKey">Clear</button><span class="feedback" id="apiKeyFeedback"></span></div>' +
        '<div class="auth-note" style="margin-top:8px">Local development default: dev-admin-key. Production should use your own ADMIN_API_KEY.</div>' +
        '</div></div>' +
        '<div class="panel"><div class="panel-head"><h2>Runtime Status</h2></div><div class="panel-body">' +
        '<div class="form-grid">' +
        field("Environment", st.environment) +
        field("Mock mode", st.mock_mode ? "ON" : "OFF") +
        field("Live calls enabled", st.live_calls_enabled ? "ON" : "OFF") +
        field("Recording", st.recording_enabled ? "ON" : "OFF") +
        field("Transcript storage", st.transcript_storage_enabled ? "ON" : "OFF") +
        field("Public Base URL", st.public_base_url) +
        '</div></div></div>' +

        '<div class="panel"><div class="panel-head"><h2>Providers</h2></div><div class="panel-body">' +
        providerCard("Telephony", p.telephony) +
        providerCard("Speech-to-Text", p.stt) +
        providerCard("Text-to-Speech", p.tts) +
        providerCard("LLM", p.llm) +
        '</div></div>' +

        '<div class="panel"><div class="panel-head"><h2>Readiness Checks</h2></div><div class="panel-body">' +
        '<div class="checks">' +
        read.checks.map(function (c) {
          return (
            '<div class="check"><div><strong>' + esc(c.label) + "</strong>" +
            '<div class="auth-note">' + esc(c.detail || "") + "</div></div>" +
            '<span class="c-status ' + (c.status === "PASS" ? "ok" : "err") + '">' + esc(c.status) + "</span></div>"
          );
        }).join("") +
        "</div></div></div>";
    }).catch(function (e) { view.innerHTML = errBox(e); });
  }

  function providerCard(title, prov) {
    if (!prov) return "";
    return (
      '<div class="summary-item"><div class="s-label">' + esc(title) + "</div>" +
      '<div class="s-value">' + esc(prov.name || "—") +
      (prov.model ? " · " + esc(prov.model) : "") +
      (prov.voice ? " · " + esc(prov.voice) : "") +
      '</div><div class="auth-note">' +
      (prov.configured ? "configured" : "NOT configured") +
      "</div></div>"
    );
  }

  // ------------------------------------------------------------- test call

  function renderTestCall(view) {
    Promise.all([loadStatus(), api("/api/v1/meta/readiness"), api("/api/v1/leads?page_size=100")])
      .then(function (r) {
        var st = r[0];
        var read = r[1];
        var leads = (r[2] && r[2].items) || [];
        var leadOptions = leads.map(function (l) {
          return '<option value="' + l.id + '">' + esc(l.name || l.phone) + " — " + esc(l.phone) + "</option>";
        }).join("");

        view.innerHTML =
          '<div class="panel"><div class="panel-head"><h2>Pre-Call Summary</h2></div><div class="panel-body">' +
          '<div class="summary">' +
          summaryItem("Telephony", st.providers.telephony.name) +
          summaryItem("STT", st.providers.stt.name) +
          summaryItem("TTS", st.providers.tts.name) +
          summaryItem("LLM", st.providers.llm.name + (st.providers.llm.model ? " (" + st.providers.llm.model + ")" : "")) +
          summaryItem("Public URL", st.public_base_url) +
          summaryItem("WebSocket", "wss://…/api/v1/ws/calls/{id}") +
          summaryItem("Live calls", st.live_calls_enabled ? "ENABLED" : "DISABLED") +
          '</div>' +
          '<div class="auth-note" style="margin-top:8px">Expected flow: Identity → permission → type/location/budget → summary → purpose/timeline → present options → visit date/time → book → hangup</div>' +
          "</div></div>" +

          '<div class="panel"><div class="panel-head"><h2>Readiness</h2></div><div class="panel-body">' +
          '<div class="checks">' +
          read.checks.map(function (c) {
            return (
              '<div class="check"><div><strong>' + esc(c.label) + "</strong>" +
              '<div class="auth-note">' + esc(c.detail || "") + "</div></div>" +
              '<span class="c-status ' + (c.status === "PASS" ? "ok" : "err") + '">' + esc(c.status) + "</span></div>"
            );
          }).join("") +
          "</div></div></div>" +

          '<div class="panel"><div class="panel-head"><h2>Start One Real Test Call</h2></div><div class="panel-body">' +
          '<div class="form-grid">' +
          '<div><label>Lead</label><select id="tcLead">' + leadOptions + "</select></div>" +
          '<div><label>Destination phone (override)</label><input id="tcPhone" placeholder="+91…" /></div>' +
          '</div>' +
          '<div class="form-actions">' +
          '<button class="btn btn-danger" id="tcStart">START ONE REAL TEST CALL</button>' +
          '<span class="feedback" id="tcFeedback"></span>' +
          "</div>" +
          '<div id="tcLog" class="logbox" style="margin-top:14px"></div>' +
          "</div></div>";

        addTestCallLog = function (line) {
          var el = $("#tcLog");
          if (el) { el.textContent += line + "\n"; el.scrollTop = el.scrollHeight; }
        };
        addTestCallLog("// Test-call console. Enter lead + phone, then press START.");
        addTestCallLog("// Live calls enabled: " + st.live_calls_enabled);

        var startBtn = $("#tcStart");
        startBtn.addEventListener("click", triggerTestCall);
      })
      .catch(function (e) { view.innerHTML = errBox(e); });
  }

  var addTestCallLog = function () {};

  function triggerTestCall() {
    var leadId = $("#tcLead") ? $("#tcLead").value : "";
    var phone = $("#tcPhone") ? $("#tcPhone").value.trim() : "";
    var fb = $("#tcFeedback");

    loadStatus().then(function (st) {
      if (!st.live_calls_enabled) {
        fb.textContent = "ERROR: LIVE_CALLS_ENABLED=false. Real calls blocked. Run a simulation instead.";
        fb.className = "feedback err";
        addTestCallLog("// BLOCKED: live calls are disabled.");
        return;
      }
      var leadName = $("#tcLead") ? $("#tcLead").selectedOptions[0].textContent : "";
      var detailsHtml =
        "<div>Lead: " + esc(leadName) + "</div>" +
        "<div>Phone: " + esc(phone || "lead.phone") + "</div>" +
        "<div>Providers: " + esc(st.providers.telephony.name) + " / " + esc(st.providers.stt.name) +
        " / " + esc(st.providers.tts.name) + " / " + esc(st.providers.llm.name) + "</div>";
      confirm(
        "This will place ONE real (paid) outbound Plivo call. Do you confirm?",
        detailsHtml,
        "Confirm the real test call"
      ).then(function (ok) {
        if (!ok) { fb.textContent = "Cancelled."; fb.className = "feedback"; return; }
        runTestCall(leadId, phone, fb);
      });
    });
  }

  function runTestCall(leadId, phone, fb) {
    var body = { lead_id: leadId, provider: "plivo", recording_enabled: true };
    if (phone) body.phone_number = phone;
    addTestCallLog("// Creating queued call for lead " + leadId);
    api("/api/v1/calls", { method: "POST", body: body }).then(function (call) {
      addTestCallLog("// Call created: " + call.id + " (status=" + call.status + ")");
      fb.textContent = "Call created: " + call.id;
      fb.className = "feedback ok";
      // Place the outbound call by advancing the state machine to INITIATED
      // (this is the choke point guarded by LIVE_CALLS_ENABLED).
      return api("/api/v1/calls/" + call.id + "/transition", {
        method: "POST",
        body: { status: "INITIATED", provider_call_id: "" },
      }).then(function (updated) {
        addTestCallLog("// Transitioned to INITIATED -> Plivo dialling " + (phone || "lead.phone"));
        addTestCallLog("// provider_call_id: " + (updated.provider_call_id || "pending"));
        fb.textContent = "Dialling… provider_call_id=" + (updated.provider_call_id || "pending");
        window.location.hash = "#/calls/" + call.id;
      });
    }).catch(function (e) {
      fb.textContent = "ERROR: " + e.message;
      fb.className = "feedback err";
      addTestCallLog("// ERROR: " + e.message);
    });
  }

  // ------------------------------------------------------------- misc

  function buildQuery(params) {
    return Object.keys(params).filter(function (k) { return params[k] !== undefined && params[k] !== ""; })
      .map(function (k) { return encodeURIComponent(k) + "=" + encodeURIComponent(params[k]); })
      .join("&");
  }

  function errBox(e) {
    return '<div class="panel"><div class="panel-body"><div class="empty">Error: ' + esc(e.message || String(e)) + "</div></div></div>";
  }

  // ------------------------------------------------------------- init

  function init() {
    $("#authNote").textContent = getKey() ? "API key set (session)" : "Enter API key: ";
    // Poll health + status for the connection badge.
    function refreshConn() {
      fetch(API + "/health").then(function (r) {
        var badge = $("#connBadge");
        if (r.ok) { badge.textContent = "API connected"; badge.className = "conn-badge ok"; }
        else { badge.textContent = "API error " + r.status; badge.className = "conn-badge bad"; }
      }).catch(function () {
        var badge = $("#connBadge");
        badge.textContent = "API offline"; badge.className = "conn-badge bad";
      });
    }
    refreshConn();
    setInterval(refreshConn, 15000);
    navigate();
  }

  // Keep a module-level hook for inline nav.
  window.tcRun = triggerTestCall;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
