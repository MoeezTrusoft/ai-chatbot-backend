"use strict";
let token = "",
    threadId = "",
    bookingRecord = null;
const $ = id => document.getElementById(id);

function notice(message, error = false) {
    $("notice").textContent = message;
    $("notice").classList.toggle("error", error);
}
async function api(path, options = {}) {
    if (!token) throw Error("Connect with your access token first.");
    const response = await fetch("/api/v1" + path, {
        ...options,
        headers: {
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            ...options.headers
        },
        cache: "no-store"
    });
    const body = await response.json();
    if (!response.ok) throw Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || body));
    return body;
}

function action(fn) {
    return async event => {
        event?.preventDefault();
        const button = event?.submitter || event?.currentTarget;
        try {
            if (button?.tagName === "BUTTON") button.disabled = true;
            await fn(event);
        } catch (e) {
            notice(e.message, true);
        } finally {
            if (button?.tagName === "BUTTON") button.disabled = false;
        }
    };
}

function text(parent, value, tag = "p") {
    const node = document.createElement(tag);
    node.textContent = value;
    parent.append(node);
    return node;
}

function button(parent, label, fn) {
    const node = document.createElement("button");
    node.type = "button";
    node.textContent = label;
    node.addEventListener("click", action(fn));
    parent.append(node);
    return node;
}

function showObject(parent, value) {
    text(parent, JSON.stringify(value, null, 2), "pre");
}

function clear(id) {
    $(id).replaceChildren();
    return $(id);
}

function label(value) {
    return String(value).replaceAll("_", " ");
}

function factList(parent, values) {
    const list = document.createElement("dl");
    for (const [key, raw] of Object.entries(values || {})) {
        const value = raw && typeof raw === "object" && !Array.isArray(raw) && "value" in raw ? raw.value : raw;
        if (value === null || value === undefined || value === "" || (Array.isArray(value) && !value.length)) continue;
        if (typeof value === "object" && !Array.isArray(value)) continue;
        text(list, label(key), "dt");
        text(list, Array.isArray(value) ? value.map(label).join(", ") : label(value), "dd");
    }
    parent.append(list);
    if (!list.children.length) text(parent, "No details confirmed yet.");
}

function renderConversation(area, data) {
    text(area, data.summary || "Conversation summary is not yet available.", "h3");
    for (const [group, values] of Object.entries(data.confirmed_facts)) {
        text(area, label(group), "h4");
        factList(area, values);
    }
    text(area, "Open questions", "h4");
    text(area, data.open_questions.length ? data.open_questions.map(label).join(" · ") : "No outstanding questions.");
    text(area, "Appointments", "h4");
    for (const item of data.appointments) text(area, item.customer_display_time + " · " + item.csr_name + " · " + label(item.status));
    if (!data.appointments.length) text(area, "No appointments yet.");
    text(area, "Actions needing attention", "h4");
    for (const item of data.failed_actions) text(area, label(item.action_type || "Action") + " · " + label(item.status));
    if (!data.failed_actions.length) text(area, "No failed actions.");
    text(area, "Manuscript progress", "h4");
    for (const item of data.intake) text(area, item.payload.filename + " · " + label(item.status));
    if (!data.intake.length) text(area, "No intake requests.");
}
async function loadAnalytics() {
    const data = await api("/staff/analytics"),
        area = clear("analytics-data");
    text(area, "Last " + data.days + " days", "h3");
    factList(area, {
        conversations: data.conversations,
        leads: data.leads,
        consultations: data.consultations,
        booked_conversations: data.booked_conversations,
        consultation_conversion: data.consultation_conversion === null ? "No conversations yet" : (data.consultation_conversion * 100).toFixed(1) + "%"
    });
    text(area, "Feedback", "h3");
    for (const row of data.feedback) text(area, (row.helpful ? "Helpful" : "Not helpful") + " · " + label(row.reason) + " · " + row.count);
    if (!data.feedback.length) text(area, "No feedback submitted yet.");
}
async function loadThreads() {
    const list = clear("thread-list");
    for (const item of await api("/staff/threads")) {
        const row = text(list, "", "div");
        row.className = "item";
        button(row, item.summary || "Conversation " + item.id.slice(0, 8), async () => {
            const detail = clear("thread-detail");
            const data = await api("/staff/threads/" + item.id);
            renderConversation(detail, data);
            button(detail, "Manage appointments and intake", () => openCustomer(item.id));
        });
        text(row, item.turn_count + " turns · " + item.sales_stage);
    }
    if (!list.children.length) text(list, "No active conversations.");
}
async function loadDeliveries() {
    const list = clear("delivery-list");
    for (const item of await api("/staff/deliveries")) {
        const row = text(list, "", "div");
        row.className = "item";
        text(row, item.appointment_id + " · " + item.attempts + " attempts");
        text(row, item.last_error || "Waiting for delivery");
        if (item.quarantined) button(row, "Replay delivery", async () => {
            await api("/staff/deliveries/" + item.id + "/replay", {
                method: "POST"
            });
            notice("Delivery queued for retry.");
            await loadDeliveries();
        });
    }
    if (!list.children.length) text(list, "No pending deliveries.");
}
async function loadKnowledge() {
    const list = clear("release-list");
    for (const item of await api("/staff/knowledge")) {
        const row = text(list, "", "div");
        row.className = "item";
        button(row, item.title, async () => {
            const area = clear("release-detail");
            area.hidden = false;
            const data = await api("/staff/knowledge/" + item.id);
            text(area, data.payload.title, "h3");
            text(area, data.payload.chunks.length + " source chunks · " + data.status);
            const query = document.createElement("input");
            query.placeholder = "Question to preview against these sources";
            area.append(query);
            button(area, "Preview evidence", async () => {
                const value = await api("/staff/knowledge/" + item.id + "/preview", {
                    method: "POST",
                    body: JSON.stringify({
                        query: query.value
                    })
                });
                result.replaceChildren();
                text(result, value.answer_preview);
                showObject(result, value.sources);
            });
            button(area, "Preview generated answer", async () => {
                const value = await api("/staff/knowledge/" + item.id + "/preview", {
                    method: "POST",
                    body: JSON.stringify({
                        query: query.value,
                        generate_answer: true
                    })
                });
                result.replaceChildren();
                text(result, value.answer_preview);
                if (value.quality) text(result, value.quality.passed ? "Quality checks passed" : "Review required: " + value.quality.failures.join(", "));
                for (const source of value.sources) text(result, source.title + " · " + source.section);
            });
            if (data.status === "draft") button(area, "Approve release", async () => {
                await api("/staff/knowledge/" + item.id + "/approve", {
                    method: "POST"
                });
                notice("Release approved.");
                await loadKnowledge();
            });
            if (data.status === "approved" || data.status === "published") button(area, data.status === "published" ? "Restore this release" : "Publish complete release", async () => {
                if (!confirm("Make this complete source release active?")) return;
                await api("/staff/knowledge/" + item.id + (data.status === "published" ? "/rollback" : "/publish"), {
                    method: "POST"
                });
                notice("Knowledge release activated.");
                await loadKnowledge();
            });
            const result = text(area, "", "div");
        });
        text(row, item.status + (item.expired ? " · Expired" : "") + " · expires " + new Date(item.expires_at).toLocaleDateString());
    }
}
async function openCustomer(id) {
    if (threadId !== id) {
        $("booking").reset();
        clear("slot");
    }
    threadId = id;
    $("thread-id").value = id;
    switchTab("customer");
    $("customer-detail").hidden = false;
    const rows = await api("/customer/threads/" + id + "/appointments");
    bookingRecord = rows.find(row => row.status === "scheduled") || null;
    const area = clear("bookings");
    for (const row of rows) {
        text(area, row.customer_display_time + " · " + row.csr_name + " · " + row.status);
        if (row.status === "scheduled") button(area, "Cancel appointment", async () => {
            if (!confirm("Cancel this consultation?")) return;
            await api("/customer/threads/" + id + "/appointments/" + row.id + "?expected_version=" + row.version, {
                method: "DELETE"
            });
            notice("Appointment cancelled.");
            await openCustomer(id);
        });
    }
    if (bookingRecord) {
        $("name").value = bookingRecord.customer_name;
        $("email").value = bookingRecord.customer_email || "";
        $("timezone").value = bookingRecord.customer_timezone;
        $("reminders").checked = bookingRecord.reminders_enabled;
    }
    const intake = clear("intakes");
    for (const row of await api("/customer/threads/" + id + "/intake")) {
        text(intake, row.payload.filename + " · " + row.status + (row.payload.error_code ? " · " + row.payload.error_code : ""));
        if (row.status === "failed" && row.payload.error_code !== "unsafe_file") button(intake, "Retry intake", async () => {
            await api("/customer/threads/" + id + "/intake/" + row.id + "/retry", {
                method: "POST"
            });
            await openCustomer(id);
        });
    }
    if (!intake.children.length) text(intake, "No manuscript intake requests.");
}

function switchTab(id) {
    for (const panel of document.querySelectorAll(".panel")) panel.hidden = panel.id !== id;
    for (const tab of document.querySelectorAll("nav button")) tab.classList.toggle("active", tab.dataset.tab === id);
}
for (const tab of document.querySelectorAll("nav button")) tab.addEventListener("click", action(async () => {
    switchTab(tab.dataset.tab);
    if (tab.dataset.tab === "conversations") await loadThreads();
    if (tab.dataset.tab === "deliveries") await loadDeliveries();
    if (tab.dataset.tab === "knowledge") await loadKnowledge();
    if (tab.dataset.tab === "analytics") await loadAnalytics();
}));
$("login").addEventListener("submit", action(async () => {
    token = $("token").value.trim();
    $("token").value = "";
    notice("Session connected.");
    if (threadId) await openCustomer(threadId);
    else if (location.pathname === "/portal") notice("Enter your conversation ID to continue.");
    else await loadThreads();
}));
$("logout").addEventListener("click", () => {
    token = "";
    location.reload();
});
$("refresh-threads").addEventListener("click", action(loadThreads));
$("customer-load").addEventListener("submit", action(() => openCustomer($("thread-id").value.trim())));
$("release-import").addEventListener("submit", action(async () => {
    const file = $("release-file").files[0];
    if (file.size > 20 * 1024 * 1024) throw Error("Release file exceeds 20 MB.");
    await api("/staff/knowledge", {
        method: "POST",
        body: await file.text()
    });
    notice("Draft created. Review it before approval.");
    await loadKnowledge();
}));
$("availability").addEventListener("submit", action(async () => {
    const slots = await api("/customer/threads/" + threadId + "/appointments/availability?day=" + $("booking-day").value + "&timezone=" + encodeURIComponent($("timezone").value));
    const select = clear("slot");
    for (const slot of slots) {
        const option = document.createElement("option");
        option.value = JSON.stringify(slot);
        option.textContent = new Date(slot.customer_time).toLocaleString(undefined, {
            timeZone: $("timezone").value,
            dateStyle: "medium",
            timeStyle: "short"
        }) + " · " + slot.csr_name;
        select.append(option);
    }
    if (!slots.length) notice("No available times on that date.");
}));
$("booking").addEventListener("submit", action(async () => {
    if (!$("slot").value) throw Error("Choose an available time first.");
    const slot = JSON.parse($("slot").value);
    const value = {
        appointment_id: bookingRecord?.id || crypto.randomUUID(),
        expected_version: bookingRecord?.version || 0,
        starts_at: slot.starts_at,
        customer_timezone: $("timezone").value,
        csr_id: slot.csr_id,
        name: $("name").value,
        email: $("email").value,
        reminders_enabled: $("reminders").checked,
        services: bookingRecord?.services || []
    };
    await api("/customer/threads/" + threadId + "/appointments", {
        method: "POST",
        body: JSON.stringify(value)
    });
    notice("Appointment saved.");
    await openCustomer(threadId);
}));
$("feedback").addEventListener("submit", action(async () => {
    await api("/customer/threads/" + threadId + "/feedback", {
        method: "PUT",
        body: JSON.stringify({
            turn_sequence: Number($("turn").value),
            helpful: $("helpful").value === "true",
            reason: $("reason").value,
            comment: $("comment").value || null
        })
    });
    notice("Thank you. Your feedback has been saved.");
}));
$("export").addEventListener("click", action(async () => {
    let after = 0,
        data, messages = [];
    do {
        data = await api("/customer/threads/" + threadId + "/export?after_sequence=" + after);
        messages.push(...data.messages);
        after = data.next_after_sequence;
    } while (after !== null);
    const url = URL.createObjectURL(new Blob([JSON.stringify({
        ...data,
        messages
    }, null, 2)], {
        type: "application/json"
    }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "bookcraft-conversation.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}));
$("delete-data").addEventListener("click", action(async () => {
    if (!confirm("Delete this conversation and its associated data? This cannot be undone.")) return;
    const result = await api("/customer/threads/" + threadId + "/data", {
        method: "DELETE"
    });
    $("customer-detail").hidden = true;
    notice("Deletion " + result.status + ". You can check its status with your conversation ID.");
}));
$("check-deletion").addEventListener("click", action(async () => {
    const id = $("thread-id").value.trim();
    if (!id) throw Error("Enter your conversation ID first.");
    const result = await api("/customer/threads/" + id + "/deletion");
    notice("Deletion status: " + label(result.status));
}));
const initial = new URLSearchParams(location.search).get("thread");
if (initial) {
    threadId = initial;
    $("thread-id").value = initial;
    switchTab("customer");
} else switchTab(location.pathname === "/portal" ? "customer" : "conversations");
