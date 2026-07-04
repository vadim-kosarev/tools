'use strict';

const { createApp, ref, computed, watch, nextTick, onMounted, onUnmounted, reactive, provide, inject, defineComponent } = Vue;
const { createRouter, createWebHistory, useRouter, useRoute, RouterView, RouterLink } = VueRouter;

// ---------------------------------------------------------------------------
// API helper
// ---------------------------------------------------------------------------
const api = {
    async request(method, url, body) {
        const opts = {
            method,
            headers: { 'Content-Type': 'application/json' },
        };
        if (body !== undefined) opts.body = JSON.stringify(body);
        const resp = await fetch(url, opts);
        if (!resp.ok) {
            const text = await resp.text().catch(() => '');
            throw new Error(`${resp.status} ${text.slice(0, 200)}`);
        }
        return resp.json();
    },
    get: (url) => api.request('GET', url),
    post: (url, body) => api.request('POST', url, body),
    put: (url, body) => api.request('PUT', url, body),
};

// ---------------------------------------------------------------------------
// Toast service
// ---------------------------------------------------------------------------
const toasts = ref([]);
let _toastId = 0;

function showToast(message, type = 'success', duration = 3500) {
    const id = ++_toastId;
    toasts.value.push({ id, message, type });
    setTimeout(() => {
        const idx = toasts.value.findIndex(t => t.id === id);
        if (idx !== -1) toasts.value.splice(idx, 1);
    }, duration);
}

// ---------------------------------------------------------------------------
// Merge modal state (global)
// ---------------------------------------------------------------------------
const mergeState = reactive({
    show: false,
    source: null,
    target: null,
    targetSearch: '',
    targetResults: [],
    targetLoading: false,
    merging: false,
});

let mergeSearchTimer = null;

async function openMerge(person) {
    mergeState.source = person;
    mergeState.target = null;
    mergeState.targetSearch = '';
    mergeState.targetResults = [];
    mergeState.show = true;
}

function closeMerge() {
    mergeState.show = false;
    mergeState.source = null;
    mergeState.target = null;
}

async function searchMergeTargets(q) {
    if (!q || q.length < 1) { mergeState.targetResults = []; return; }
    mergeState.targetLoading = true;
    try {
        const data = await api.get(`/api/persons?q=${encodeURIComponent(q)}&limit=20&named_only=true`);
        mergeState.targetResults = data.items.filter(p => p.id !== mergeState.source?.id);
    } catch (e) {
        mergeState.targetResults = [];
    } finally {
        mergeState.targetLoading = false;
    }
}

watch(() => mergeState.targetSearch, (val) => {
    clearTimeout(mergeSearchTimer);
    mergeSearchTimer = setTimeout(() => searchMergeTargets(val), 300);
});

async function confirmMerge(router) {
    if (!mergeState.source || !mergeState.target) return;
    mergeState.merging = true;
    try {
        const data = await api.post('/api/persons/merge', {
            source_id: mergeState.source.id,
            target_id: mergeState.target.id,
        });
        showToast(`Merged ${data.merged_face_count} faces into "${data.target_name}"`, 'success');
        closeMerge();
        // Navigate to target person to see the merged result
        if (router) router.push(`/persons/${mergeState?.target?.id || ''}`);
    } catch (e) {
        showToast(`Merge failed: ${e.message}`, 'error');
    } finally {
        mergeState.merging = false;
    }
}

// ---------------------------------------------------------------------------
// Components
// ---------------------------------------------------------------------------

const ToastContainer = defineComponent({
    setup() { return { toasts }; },
    template: `
    <div class="toast-container">
        <div v-for="t in toasts" :key="t.id" :class="['toast', t.type]">{{ t.message }}</div>
    </div>
    `,
});

const MergeModal = defineComponent({
    setup() {
        const router = useRouter();
        return { mergeState, closeMerge, confirmMerge: () => confirmMerge(router) };
    },
    template: `
    <div class="modal-overlay" v-if="mergeState.show" @click.self="closeMerge">
        <div class="modal">
            <div class="modal-title">Merge Persons</div>

            <div class="merge-persons">
                <div class="merge-person-box">
                    <div class="mp-label">Source (will be removed)</div>
                    <img v-if="mergeState.source" :src="mergeState.source.thumb_url" alt="">
                    <div class="mp-name" :class="{ unnamed: !mergeState.source?.name }">
                        {{ mergeState.source?.name || 'Unknown' }}
                    </div>
                    <div class="mp-count">{{ mergeState.source?.face_count }} faces</div>
                </div>
                <div class="merge-arrow">→</div>
                <div class="merge-person-box">
                    <div class="mp-label">Target (will absorb)</div>
                    <img v-if="mergeState.target" :src="mergeState.target.thumb_url" alt=""
                         style="border: 2px solid var(--accent)">
                    <div v-if="!mergeState.target" style="width:80px;height:80px;border-radius:8px;background:var(--bg-card);margin:0 auto 8px;display:flex;align-items:center;justify-content:center;color:var(--text-muted);font-size:28px">?</div>
                    <div class="mp-name" :class="{ unnamed: !mergeState.target?.name }">
                        {{ mergeState.target?.name || (mergeState.target ? 'Unknown' : 'Select below') }}
                    </div>
                    <div class="mp-count" v-if="mergeState.target">{{ mergeState.target.face_count }} faces</div>
                </div>
            </div>

            <div class="merge-target-search">
                <label>Search target person:</label>
                <div class="search-bar" style="width:100%;height:38px">
                    <span class="search-icon">&#128269;</span>
                    <input v-model="mergeState.targetSearch" placeholder="Type a name..." autofocus>
                </div>
                <div v-if="mergeState.targetResults.length" class="merge-search-results">
                    <div
                        v-for="p in mergeState.targetResults" :key="p.id"
                        :class="['merge-result-row', { selected: mergeState.target?.id === p.id }]"
                        @click="mergeState.target = p"
                    >
                        <img :src="p.thumb_url" alt="">
                        <div>
                            <div class="mr-name">{{ p.name }}</div>
                            <div class="mr-count">{{ p.face_count }} faces</div>
                        </div>
                    </div>
                </div>
                <div v-if="mergeState.targetLoading" style="font-size:12px;color:var(--text-muted);padding:8px 0">Searching...</div>
            </div>

            <div class="modal-footer">
                <button class="btn btn-ghost" @click="closeMerge">Cancel</button>
                <button
                    class="btn btn-danger"
                    :disabled="!mergeState.target || mergeState.merging"
                    @click="confirmMerge"
                >
                    {{ mergeState.merging ? 'Merging...' : 'Confirm Merge' }}
                </button>
            </div>
        </div>
    </div>
    `,
});

// ---------------------------------------------------------------------------
// Views
// ---------------------------------------------------------------------------

const PersonsView = defineComponent({
    setup() {
        const router = useRouter();
        const persons = ref([]);
        const total = ref(0);
        const page = ref(1);
        const loading = ref(false);
        const search = ref('');
        const namedOnly = ref(false);
        let searchTimer = null;

        const hasMore = computed(() => persons.value.length < total.value);

        async function load(reset = false) {
            if (loading.value) return;
            if (reset) { page.value = 1; persons.value = []; }
            loading.value = true;
            try {
                const q = search.value ? `&q=${encodeURIComponent(search.value)}` : '';
                const named = namedOnly.value ? '&named_only=true' : '';
                const data = await api.get(`/api/persons?page=${page.value}&limit=50${q}${named}`);
                if (reset) persons.value = data.items;
                else persons.value.push(...data.items);
                total.value = data.total;
                page.value++;
            } catch (e) {
                showToast('Failed to load persons: ' + e.message, 'error');
            } finally {
                loading.value = false;
            }
        }

        function onSearch() {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => load(true), 350);
        }

        onMounted(() => load(true));
        watch(namedOnly, () => load(true));

        return { persons, total, loading, hasMore, search, namedOnly, load, onSearch, router, openMerge };
    },
    template: `
    <div class="view">
        <div class="view-header">
            <div class="view-title">Persons</div>
            <div class="view-count">{{ total }} total</div>
            <div class="view-actions">
                <div class="search-bar">
                    <span class="search-icon">&#128269;</span>
                    <input v-model="search" @input="onSearch" placeholder="Search name...">
                </div>
                <button
                    class="btn btn-ghost"
                    :class="{ 'btn-primary': namedOnly }"
                    @click="namedOnly = !namedOnly"
                    style="font-size:13px;padding:6px 12px"
                >Named only</button>
            </div>
        </div>

        <div v-if="loading && !persons.length" class="loading">
            <div class="spinner"></div>
            Loading persons...
        </div>
        <div v-else-if="!persons.length" class="empty">
            <div class="empty-icon">&#128100;</div>
            <div class="empty-text">No persons found</div>
        </div>
        <div v-else>
            <div class="card-grid">
                <div v-for="p in persons" :key="p.id" class="person-card">
                    <img class="card-thumb" :src="p.thumb_url"
                         onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 180 180%22><rect fill=%22%231e293b%22 width=%22180%22 height=%22180%22/><text x=%2290%22 y=%2298%22 text-anchor=%22middle%22 fill=%22%23475569%22 font-size=%2260%22>&#128100;</text></svg>'"
                         @click="router.push('/persons/' + p.id)"
                         :alt="p.name || 'Unknown'">
                    <div class="card-info" @click="router.push('/persons/' + p.id)">
                        <div class="card-name" :class="{ unnamed: !p.name }">{{ p.name || 'Unknown' }}</div>
                        <div class="card-meta">{{ p.face_count }} faces</div>
                    </div>
                    <div class="card-actions">
                        <button class="btn-sm" @click.stop="router.push('/persons/' + p.id)">View</button>
                        <button class="btn-sm primary" @click.stop="openMerge(p)">Merge</button>
                    </div>
                </div>
            </div>
            <div class="load-more" v-if="hasMore">
                <button class="btn btn-ghost" @click="load()" :disabled="loading">
                    {{ loading ? 'Loading...' : 'Load more' }}
                </button>
            </div>
        </div>
    </div>
    `,
});

const PersonDetailView = defineComponent({
    setup() {
        const route = useRoute();
        const router = useRouter();
        const person = ref(null);
        const loading = ref(true);

        async function load() {
            loading.value = true;
            try {
                person.value = await api.get(`/api/persons/${route.params.id}`);
            } catch (e) {
                showToast('Failed to load person: ' + e.message, 'error');
            } finally {
                loading.value = false;
            }
        }

        onMounted(load);
        watch(() => route.params.id, load);

        return { person, loading, router, openMerge };
    },
    template: `
    <div class="view">
        <div class="back-link" @click="router.push('/persons')">&#8592; Persons</div>

        <div v-if="loading" class="loading"><div class="spinner"></div>Loading...</div>
        <div v-else-if="!person" class="empty"><div class="empty-icon">&#9888;</div><div class="empty-text">Not found</div></div>
        <div v-else>
            <div class="person-detail-header">
                <img class="person-detail-thumb" :src="person.thumb_url"
                     onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 120 120%22><rect fill=%22%231e293b%22 width=%22120%22 height=%22120%22/><text x=%2260%22 y=%2274%22 text-anchor=%22middle%22 fill=%22%23475569%22 font-size=%2250%22>&#128100;</text></svg>'"
                     :alt="person.name || 'Unknown'">
                <div class="person-detail-info">
                    <div class="person-detail-name" :class="{ unnamed: !person.name }">
                        {{ person.name || 'Unknown' }}
                    </div>
                    <div class="person-detail-meta">{{ person.face_count }} faces detected</div>
                    <div class="person-detail-actions">
                        <button class="btn btn-primary" @click="openMerge(person)">Merge into another</button>
                    </div>
                </div>
            </div>

            <div class="section-title">Face crops ({{ person.faces.length }})</div>
            <div v-if="!person.faces.length" class="empty" style="padding:30px">
                <div class="empty-text">No face crops found</div>
            </div>
            <div class="face-grid">
                <div v-for="f in person.faces" :key="f.id" class="face-card"
                     @click="router.push('/assets/' + f.assetId)">
                    <img :src="f.crop_url"
                         onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 120 120%22><rect fill=%22%231e293b%22 width=%22120%22 height=%22120%22/><text x=%2260%22 y=%2274%22 text-anchor=%22middle%22 fill=%22%23475569%22 font-size=%2240%22>?</text></svg>'"
                         :alt="'Face crop'">
                    <div class="face-meta">
                        <span class="face-score" v-if="f.score">{{ (f.score * 100).toFixed(0) }}%</span>
                    </div>
                </div>
            </div>
        </div>
    </div>
    `,
});

const AssetsView = defineComponent({
    setup() {
        const router = useRouter();
        const assets = ref([]);
        const total = ref(0);
        const page = ref(1);
        const loading = ref(false);

        const hasMore = computed(() => assets.value.length < total.value);

        async function load(reset = false) {
            if (loading.value) return;
            if (reset) { page.value = 1; assets.value = []; }
            loading.value = true;
            try {
                const data = await api.get(`/api/assets/with-faces?page=${page.value}&limit=50`);
                if (reset) assets.value = data.items;
                else assets.value.push(...data.items);
                total.value = data.total;
                page.value++;
            } catch (e) {
                showToast('Failed to load assets: ' + e.message, 'error');
            } finally {
                loading.value = false;
            }
        }

        onMounted(() => load(true));

        function namedPct(a) {
            if (!a.face_count) return 0;
            return Math.round((a.named_count / a.face_count) * 100);
        }

        return { assets, total, loading, hasMore, load, router, namedPct };
    },
    template: `
    <div class="view">
        <div class="view-header">
            <div class="view-title">Assets with faces</div>
            <div class="view-count">{{ total }} assets</div>
        </div>

        <div v-if="loading && !assets.length" class="loading"><div class="spinner"></div>Loading...</div>
        <div v-else-if="!assets.length" class="empty">
            <div class="empty-icon">&#128444;</div>
            <div class="empty-text">No assets with faces</div>
        </div>
        <div v-else>
            <div class="card-grid">
                <div v-for="a in assets" :key="a.id" class="asset-card"
                     @click="router.push('/assets/' + a.id)">
                    <img class="card-thumb" :src="a.thumb_url"
                         onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 160 160%22><rect fill=%22%231e293b%22 width=%22160%22 height=%22160%22/><text x=%2280%22 y=%2290%22 text-anchor=%22middle%22 fill=%22%23475569%22 font-size=%2250%22>&#128444;</text></svg>'"
                         :alt="a.originalFileName">
                    <div class="face-badge">{{ a.face_count }} face{{ a.face_count !== 1 ? 's' : '' }}</div>
                    <div class="card-info">
                        <div class="card-name">{{ a.originalFileName }}</div>
                        <div class="named-bar">
                            <div class="fill" :style="{ width: namedPct(a) + '%' }"></div>
                        </div>
                        <div style="font-size:11px;color:var(--text-muted);margin-top:4px">
                            {{ a.named_count }}/{{ a.face_count }} named
                        </div>
                    </div>
                </div>
            </div>
            <div class="load-more" v-if="hasMore">
                <button class="btn btn-ghost" @click="load()" :disabled="loading">
                    {{ loading ? 'Loading...' : 'Load more' }}
                </button>
            </div>
        </div>
    </div>
    `,
});

const AssetDetailView = defineComponent({
    setup() {
        const route = useRoute();
        const router = useRouter();
        const faces = ref([]);
        const loading = ref(true);
        const assetId = computed(() => route.params.id);
        const thumbUrl = computed(() => `/api/assets/${assetId.value}/thumbnail`);
        const previewUrl = computed(() => `/api/assets/${assetId.value}/thumbnail`);

        async function load() {
            loading.value = true;
            try {
                const data = await api.get(`/api/assets/${assetId.value}/faces`);
                faces.value = data.faces;
            } catch (e) {
                showToast('Failed to load asset faces: ' + e.message, 'error');
            } finally {
                loading.value = false;
            }
        }

        onMounted(load);
        watch(assetId, load);

        return { faces, loading, router, assetId, thumbUrl, previewUrl };
    },
    template: `
    <div class="view">
        <div class="back-link" @click="router.push('/assets')">&#8592; Assets</div>

        <div v-if="loading" class="loading"><div class="spinner"></div>Loading...</div>
        <div v-else class="asset-detail-layout">
            <div class="asset-preview">
                <img :src="previewUrl"
                     onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 400 300%22><rect fill=%22%231e293b%22 width=%22400%22 height=%22300%22/><text x=%22200%22 y=%22160%22 text-anchor=%22middle%22 fill=%22%23475569%22 font-size=%2260%22>&#128444;</text></svg>'"
                     alt="Asset preview">
            </div>
            <div class="asset-faces-sidebar">
                <h3>Faces ({{ faces.length }})</h3>
                <div v-if="!faces.length" class="empty" style="padding:20px 0">
                    <div class="empty-text">No faces detected</div>
                </div>
                <div v-for="f in faces" :key="f.id" class="face-row"
                     @click="f.person_id && router.push('/persons/' + f.person_id)">
                    <img :src="f.crop_url"
                         onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 48 48%22><rect fill=%22%231e293b%22 width=%2248%22 height=%2248%22/><text x=%2224%22 y=%2230%22 text-anchor=%22middle%22 fill=%22%23475569%22 font-size=%2222%22>?</text></svg>'"
                         alt="">
                    <div class="face-info">
                        <div class="face-person" :class="{ unknown: !f.person_name }">
                            {{ f.person_name || 'Unknown' }}
                        </div>
                        <div class="face-score-sm" v-if="f.score">{{ (f.score * 100).toFixed(0) }}% confidence</div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    `,
});

const UnassignedView = defineComponent({
    setup() {
        const router = useRouter();
        const faces = ref([]);
        const total = ref(0);
        const page = ref(1);
        const loading = ref(false);

        const hasMore = computed(() => faces.value.length < total.value);

        async function load(reset = false) {
            if (loading.value) return;
            if (reset) { page.value = 1; faces.value = []; }
            loading.value = true;
            try {
                const data = await api.get(`/api/faces/unassigned?page=${page.value}&limit=100`);
                if (reset) faces.value = data.items;
                else faces.value.push(...data.items);
                total.value = data.total;
                page.value++;
            } catch (e) {
                showToast('Failed to load unassigned faces: ' + e.message, 'error');
            } finally {
                loading.value = false;
            }
        }

        onMounted(() => load(true));

        return { faces, total, loading, hasMore, load, router };
    },
    template: `
    <div class="view">
        <div class="view-header">
            <div class="view-title">Unassigned faces</div>
            <div class="view-count">{{ total }} total</div>
        </div>

        <div v-if="loading && !faces.length" class="loading"><div class="spinner"></div>Loading...</div>
        <div v-else-if="!faces.length" class="empty">
            <div class="empty-icon">&#127881;</div>
            <div class="empty-text">All faces are assigned to persons</div>
        </div>
        <div v-else>
            <div class="face-grid">
                <div v-for="f in faces" :key="f.id" class="face-card"
                     @click="router.push('/assets/' + f.assetId)">
                    <img :src="f.crop_url"
                         onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 120 120%22><rect fill=%22%231e293b%22 width=%22120%22 height=%22120%22/><text x=%2260%22 y=%2274%22 text-anchor=%22middle%22 fill=%22%23475569%22 font-size=%2240%22>?</text></svg>'"
                         alt="Unassigned face">
                    <div class="face-meta">
                        <span class="face-score" v-if="f.score">{{ (f.score * 100).toFixed(0) }}%</span>
                    </div>
                </div>
            </div>
            <div class="load-more" v-if="hasMore">
                <button class="btn btn-ghost" @click="load()" :disabled="loading">
                    {{ loading ? 'Loading...' : 'Load more' }}
                </button>
            </div>
        </div>
    </div>
    `,
});

const MergeLogView = defineComponent({
    setup() {
        const items = ref([]);
        const loading = ref(true);

        function formatDate(iso) {
            if (!iso) return '';
            return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' });
        }

        onMounted(async () => {
            try {
                const data = await api.get('/api/merge-log?limit=200');
                items.value = data.items;
            } catch (e) {
                showToast('Failed to load merge log: ' + e.message, 'error');
            } finally {
                loading.value = false;
            }
        });

        return { items, loading, formatDate };
    },
    template: `
    <div class="view">
        <div class="view-header">
            <div class="view-title">Merge Log</div>
            <div class="view-count">{{ items.length }} entries</div>
        </div>

        <div v-if="loading" class="loading"><div class="spinner"></div>Loading...</div>
        <div v-else-if="!items.length" class="empty">
            <div class="empty-icon">&#128221;</div>
            <div class="empty-text">No merges performed yet</div>
        </div>
        <table v-else class="merge-table">
            <thead>
                <tr>
                    <th>Source (removed)</th>
                    <th></th>
                    <th>Target (kept)</th>
                    <th>Faces moved</th>
                    <th>Date</th>
                </tr>
            </thead>
            <tbody>
                <tr v-for="(item, idx) in items" :key="idx">
                    <td>{{ item.source_person_name || '(unknown)' }}</td>
                    <td class="arrow-cell">&#8594;</td>
                    <td>{{ item.target_person_name || '(unknown)' }}</td>
                    <td>{{ item.face_count_moved }}</td>
                    <td>{{ formatDate(item.merged_at) }}</td>
                </tr>
            </tbody>
        </table>
    </div>
    `,
});

// ---------------------------------------------------------------------------
// FfPersonModal — person detail popup (used from Videos view)
// ---------------------------------------------------------------------------
const ffPersonModal = reactive({ show: false, person: null, loading: false, priorityVideoId: null, priorityTrackId: null });

async function openFfPerson(localPersonId, priorityVideoId = null, priorityTrackId = null) {
    closePhotoTooltip();
    ffPersonModal.show = true;
    ffPersonModal.person = null;
    ffPersonModal.loading = true;
    ffPersonModal.priorityVideoId = priorityVideoId;
    ffPersonModal.priorityTrackId = priorityTrackId;
    try {
        ffPersonModal.person = await api.get(`/api/ff/persons/${localPersonId}`);
    } catch (e) {
        showToast('Failed to load person: ' + e.message, 'error');
        ffPersonModal.show = false;
    } finally {
        ffPersonModal.loading = false;
    }
}

const FfPersonModal = defineComponent({
    setup() {
        const router = useRouter();

        function close() { ffPersonModal.show = false; }

        function goToPersons() {
            close();
            router.push({ path: '/ff/persons', query: { q: ffPersonModal.person?.immich_person_name || ffPersonModal.person?.label } });
        }

        function goToFile(filename) {
            close();
            router.push({ path: '/videos', query: { q: filename } });
        }

        function formatDate(iso) {
            if (!iso) return '';
            return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' });
        }

        // Priority file first in files list
        const sortedFiles = computed(() => {
            const files = ffPersonModal.person?.files || [];
            const pvid = ffPersonModal.priorityVideoId;
            if (!pvid) return files;
            const pri = files.filter(f => f.video_id === pvid);
            const rest = files.filter(f => f.video_id !== pvid);
            return [...pri, ...rest];
        });

        // Scroll priority file into view after data loads
        const fileRefs = {};
        watch(() => ffPersonModal.person, async (person) => {
            if (!person || !ffPersonModal.priorityVideoId) return;
            await nextTick();
            const el = fileRefs[ffPersonModal.priorityVideoId];
            if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        });

        const BLANK = `data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect fill="%231e293b" width="64" height="64"/><text x="32" y="42" text-anchor="middle" fill="%23475569" font-size="28">&#128100;</text></svg>`;

        function photoItem(seg, file) {
            return {
                thumb_url: seg.thumb_url,
                filename: file.filename,
                frame_index: seg.frame_index ?? null,
                total_frames: file.total_frames ?? null,
                fps: file.fps ?? null,
                start_time: file.start_time ?? null,
            };
        }
        const photoGallery = computed(() =>
            sortedFiles.value.flatMap(f2 => (f2.segments || []).map(s => photoItem(s, f2)))
        );

        return { m: ffPersonModal, close, goToPersons, goToFile, openPhoto, formatDate, BLANK, sortedFiles, fileRefs, photoItem, photoGallery };
    },
    template: `
    <div class="modal-overlay" v-if="m.show" @click.self="close">
        <div class="modal ffp-modal">
            <div v-if="m.loading" class="loading" style="padding:40px"><div class="spinner"></div>Loading...</div>
            <div v-else-if="m.person">
                <div class="ffp-header">
                    <img class="ffp-avatar"
                         :src="m.person.best_faces[0]?.thumb_url || BLANK"
                         :onerror="'this.src=\\''+BLANK+'\\''"
                         style="cursor:zoom-in"
                         @click="openPhoto(m.person.best_faces[0], m.person.best_faces)">
                    <div class="ffp-info">
                        <div class="ffp-name">{{ m.person.immich_person_name || m.person.label }}</div>
                        <div class="ffp-meta">
                            {{ m.person.track_count }} tracks ·
                            {{ m.person.files.length }} files ·
                            {{ m.person.distinct_days }} day{{ m.person.distinct_days !== 1 ? 's' : '' }}
                        </div>
                    </div>
                    <button class="btn btn-ghost" style="margin-left:auto" @click="goToPersons">All appearances →</button>
                    <button class="ffp-close" @click="close">✕</button>
                </div>

                <div class="section-title" style="margin-top:16px">Files ({{ m.person.files.length }})</div>
                <div class="ffp-files">
                    <div v-for="f in sortedFiles" :key="f.video_id"
                         class="ff-file-row"
                         :class="{ 'ff-file-row--active': f.video_id === m.priorityVideoId }"
                         :ref="el => { if (el) fileRefs[f.video_id] = el }">
                        <div class="ff-file-name ffp-file-link" :title="f.filename" @click="goToFile(f.filename)">
                            {{ f.filename }}
                            <span style="color:var(--text-muted);font-size:10px;margin-left:6px">{{ formatDate(f.start_time) }}</span>
                        </div>
                        <div class="ff-file-thumbs">
                            <img v-for="s in f.segments" :key="s.segment_id ?? ('ft'+s.face_track_id)"
                                 :src="s.thumb_url"
                                 :title="((s.quality||0)*100).toFixed(0) + '%'"
                                 style="cursor:zoom-in"
                                 @click="openPhoto(photoItem(s, f), photoGallery)"
                                 :onerror="'this.src=\\''+BLANK+'\\''">
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    `,
});

// ---------------------------------------------------------------------------
// PhotoModal — fullscreen image viewer with prev/next navigation
// ---------------------------------------------------------------------------
const photoModal = reactive({ show: false, url: null, items: [], index: 0, mode: 'modal', tooltipX: 0, tooltipY: 0, tooltipAbove: false });

// item: { thumb_url, filename?, frame_index?, total_frames?, fps?, start_time? }
// or plain string URL (backward compat)
function openPhoto(item, items = null) {
    clearTimeout(_hoverTimer);
    const norm = i => (typeof i === 'string') ? { thumb_url: i } : (i || {});
    const normItem = norm(item);
    const list = (items && items.length) ? items.map(norm) : [normItem];
    const idx = list.findIndex(i => i.thumb_url === normItem.thumb_url);
    photoModal.items = list;
    photoModal.index = idx >= 0 ? idx : 0;
    photoModal.url = list[photoModal.index]?.thumb_url || null;
    photoModal.mode = 'modal';
    photoModal.show = true;
}

function prevPhoto() {
    if (photoModal.index > 0) {
        photoModal.index--;
        photoModal.url = photoModal.items[photoModal.index].thumb_url;
    }
}

function nextPhoto() {
    if (photoModal.index < photoModal.items.length - 1) {
        photoModal.index++;
        photoModal.url = photoModal.items[photoModal.index].thumb_url;
    }
}

let _hoverTimer = null;

function openPhotoTooltip(event, item) {
    clearTimeout(_hoverTimer);
    const normItem = typeof item === 'string' ? { thumb_url: item } : item;
    _hoverTimer = setTimeout(() => {
        photoModal.items = [normItem];
        photoModal.index = 0;
        photoModal.url = normItem.thumb_url;
        photoModal.mode = 'tooltip';
        photoModal.tooltipX = event.clientX;
        photoModal.tooltipY = event.clientY;
        photoModal.tooltipAbove = (event.clientY + 380) > window.innerHeight;
        photoModal.show = true;
    }, 550);
}

function closePhotoTooltip() {
    clearTimeout(_hoverTimer);
    if (photoModal.mode === 'tooltip') {
        photoModal.show = false;
    }
}

const PhotoModal = defineComponent({
    setup() {
        function close() { photoModal.show = false; }

        const currentItem = computed(() => photoModal.items[photoModal.index] || {});

        const timelinePct = computed(() => {
            const it = currentItem.value;
            if (it.frame_index == null || !it.total_frames) return null;
            return +(it.frame_index / it.total_frames * 100).toFixed(1);
        });

        const timeStr = computed(() => {
            const it = currentItem.value;
            if (it.frame_index == null || !it.fps) return '';
            const totalSecs = Math.round(it.frame_index / it.fps);
            const h = Math.floor(totalSecs / 3600);
            const m = Math.floor((totalSecs % 3600) / 60);
            const s = totalSecs % 60;
            const hms = h > 0
                ? `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`
                : `${m}:${String(s).padStart(2,'0')}`;
            if (!it.start_time) return hms;
            const dt = new Date(it.start_time);
            dt.setSeconds(dt.getSeconds() + totalSecs);
            return dt.toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'medium' }) + ' · ' + hms;
        });

        return { m: photoModal, close, prev: prevPhoto, next: nextPhoto, currentItem, timelinePct, timeStr };
    },
    template: `
<div class="modal-overlay photo-overlay" v-if="m.show && m.mode === 'modal'" @click="close">
    <div class="photo-modal-top" v-if="currentItem.filename">{{ currentItem.filename }}</div>
    <button class="photo-nav photo-nav-prev" v-if="m.index > 0" @click.stop="prev">&#8592;</button>
    <img :src="m.url" class="photo-fullsize" @click.stop>
    <button class="photo-nav photo-nav-next" v-if="m.index < m.items.length - 1" @click.stop="next">&#8594;</button>
    <div class="photo-modal-bottom" v-if="timeStr || timelinePct != null">
        <div class="photo-time" v-if="timeStr">{{ timeStr }}</div>
        <div class="photo-timeline" v-if="timelinePct != null">
            <div class="photo-timeline-track">
                <div class="photo-timeline-marker" :style="{left: timelinePct + '%'}"></div>
            </div>
        </div>
    </div>
    <button class="photo-close-btn" @click.stop="close">&#10005;</button>
</div>
<div :class="['photo-tooltip', m.tooltipAbove ? 'above' : '']"
     v-if="m.show && m.mode === 'tooltip'"
     :style="{left: m.tooltipX + 'px', top: m.tooltipY + 'px'}">
    <div class="photo-tooltip-header" v-if="currentItem.filename">{{ currentItem.filename }}</div>
    <img :src="m.url" class="photo-tooltip-img">
    <div class="photo-tooltip-footer" v-if="timeStr || timelinePct != null">
        <div class="photo-time" v-if="timeStr">{{ timeStr }}</div>
        <div class="photo-timeline" v-if="timelinePct != null">
            <div class="photo-timeline-track">
                <div class="photo-timeline-marker" :style="{left: timelinePct + '%'}"></div>
            </div>
        </div>
    </div>
</div>
    `,
});

// ---------------------------------------------------------------------------
// FfPersonsView
// ---------------------------------------------------------------------------
const FfPersonsView = defineComponent({
    setup() {
        const route = useRoute();
        const persons = ref([]);
        const total = ref(0);
        const page = ref(1);
        const loading = ref(false);
        const search = ref(route.query.q || '');
        const fileFilter = ref('');
        const sort = ref('tracks_desc');
        const expanded = reactive({});
        let searchTimer = null;

        const hasMore = computed(() => persons.value.length < total.value);

        const sortOptions = [
            { value: 'tracks_desc', label: 'Most tracks' },
            { value: 'tracks_asc',  label: 'Fewest tracks' },
            { value: 'days_desc',   label: 'Most days' },
            { value: 'days_asc',    label: 'Fewest days' },
            { value: 'name_asc',    label: 'Name A→Z' },
            { value: 'name_desc',   label: 'Name Z→A' },
        ];

        const dayFilters = [1, 2, 5, 10, 20];
        const minDays = ref(1);

        async function load(reset = false) {
            if (loading.value) return;
            if (reset) { page.value = 1; persons.value = []; }
            loading.value = true;
            try {
                const q = search.value ? `&q=${encodeURIComponent(search.value)}` : '';
                const days = minDays.value > 1 ? `&min_days=${minDays.value}` : '';
                const data = await api.get(`/api/ff/persons?page=${page.value}&limit=20&sort=${sort.value}${q}${days}`);
                if (reset) persons.value = data.items;
                else persons.value.push(...data.items);
                total.value = data.total;
                page.value++;
            } catch (e) {
                showToast('Failed to load persons: ' + e.message, 'error');
            } finally {
                loading.value = false;
            }
        }

        function onSearch() {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => load(true), 350);
        }

        function toggle(id) { expanded[id] = !expanded[id]; }

        function visibleFiles(files) {
            if (!fileFilter.value) return files;
            const q = fileFilter.value.toLowerCase();
            return files.filter(f => f.filename.toLowerCase().includes(q));
        }

        watch(sort, () => load(true));
        watch(minDays, () => load(true));
        onMounted(() => load(true));

        const BLANK = `data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect fill="%231e293b" width="64" height="64"/><text x="32" y="42" text-anchor="middle" fill="%23475569" font-size="28">&#128100;</text></svg>`;

        return { persons, total, loading, hasMore, load, search, fileFilter, sort, sortOptions,
                 onSearch, toggle, expanded, visibleFiles, BLANK, dayFilters, minDays };
    },
    template: `
    <div class="view">
        <div class="view-header">
            <div class="view-title">Persons</div>
            <div class="view-count">{{ total }} total</div>
            <div class="view-actions">
                <div class="search-bar">
                    <span class="search-icon">&#128269;</span>
                    <input v-model="search" @input="onSearch" placeholder="Search person...">
                </div>
                <div class="search-bar">
                    <span class="search-icon">&#127902;</span>
                    <input v-model="fileFilter" placeholder="Filter by file...">
                </div>
                <select class="sort-select" v-model="sort">
                    <option v-for="o in sortOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
                </select>
            </div>
        </div>
        <div class="day-filter-bar">
            <span class="day-filter-label">Days seen:</span>
            <button v-for="d in dayFilters" :key="d"
                    :class="['day-filter-btn', { active: minDays === d }]"
                    @click="minDays = d">
                {{ d === 1 ? 'Any' : d + '+' }}
            </button>
        </div>

        <div v-if="loading && !persons.length" class="loading"><div class="spinner"></div>Loading...</div>
        <div v-else-if="!persons.length" class="empty">
            <div class="empty-icon">&#128100;</div>
            <div class="empty-text">No persons found</div>
        </div>
        <div v-else>
            <div class="ff-person-list">
                <div v-for="p in persons" :key="p.id" class="ff-person-card">
                    <div class="ff-person-header" @click="toggle(p.id)">
                        <img class="ff-person-avatar"
                             :src="p.best_thumb_url || BLANK"
                             :onerror="'this.src=\\''+BLANK+'\\''">
                        <div class="ff-person-info">
                            <div class="ff-person-name">{{ p.immich_person_name || p.label }}</div>
                            <div class="ff-person-meta">{{ p.track_count }} tracks · {{ p.files.length }} files · {{ p.distinct_days }} day{{ p.distinct_days !== 1 ? 's' : '' }}</div>
                        </div>
                        <span class="ff-chevron">{{ expanded[p.id] ? '▾' : '▸' }}</span>
                    </div>
                    <div class="ff-person-files" v-if="expanded[p.id]">
                        <div v-for="f in visibleFiles(p.files)" :key="f.video_id" class="ff-file-row">
                            <div class="ff-file-name" :title="f.filename">{{ f.filename }}</div>
                            <div class="ff-file-thumbs">
                                <img v-for="t in f.tracks" :key="t.track_id"
                                     :src="t.thumb_url"
                                     :title="'quality: ' + (t.best_quality * 100).toFixed(0) + '%'"
                                     :onerror="'this.src=\\''+BLANK+'\\''">
                            </div>
                        </div>
                        <div v-if="visibleFiles(p.files).length === 0 && fileFilter" class="vf-no-persons">
                            No files match "{{ fileFilter }}"
                        </div>
                    </div>
                </div>
            </div>
            <div class="load-more" v-if="hasMore">
                <button class="btn btn-ghost" @click="load()" :disabled="loading">
                    {{ loading ? 'Loading...' : 'Load more' }}
                </button>
            </div>
        </div>
    </div>
    `,
});

// ---------------------------------------------------------------------------
// VideoFilesView
// ---------------------------------------------------------------------------
const VideoFilesView = defineComponent({
    setup() {
        const route = useRoute();
        const files = ref([]);
        const total = ref(0);
        const page = ref(1);
        const loading = ref(false);
        const search = ref(route.query.q || '');
        const sort = ref('date_desc');
        let searchTimer = null;

        const hasMore = computed(() => files.value.length < total.value);

        const sortOptions = [
            { value: 'date_desc',    label: 'Newest first' },
            { value: 'date_asc',     label: 'Oldest first' },
            { value: 'name_asc',     label: 'Name A→Z' },
            { value: 'name_desc',    label: 'Name Z→A' },
            { value: 'persons_desc', label: 'Most persons' },
            { value: 'persons_asc',  label: 'Fewest persons' },
        ];

        async function load(reset = false) {
            if (loading.value) return;
            if (reset) { page.value = 1; files.value = []; }
            loading.value = true;
            try {
                const q = search.value ? `&q=${encodeURIComponent(search.value)}` : '';
                const data = await api.get(`/api/ff/video-files?page=${page.value}&limit=50&sort=${sort.value}${q}`);
                if (reset) files.value = data.items;
                else files.value.push(...data.items);
                total.value = data.total;
                page.value++;
            } catch (e) {
                showToast('Failed to load video files: ' + e.message, 'error');
            } finally {
                loading.value = false;
            }
        }

        function onSearch() {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => load(true), 350);
        }

        watch(sort, () => load(true));
        onMounted(() => load(true));

        function formatDate(iso) {
            if (!iso) return '';
            return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' });
        }

        function duration(frames, fps) {
            if (!frames || !fps) return '';
            const secs = Math.round(frames / fps);
            const m = Math.floor(secs / 60);
            const s = secs % 60;
            return `${m}:${s.toString().padStart(2, '0')}`;
        }

        return { files, total, loading, hasMore, load, search, sort, sortOptions, onSearch, formatDate, duration, openFfPerson, openPhotoTooltip, closePhotoTooltip };
    },
    template: `
    <div class="view">
        <div class="view-header">
            <div class="view-title">Video Files</div>
            <div class="view-count">{{ total }} processed</div>
            <div class="view-actions">
                <div class="search-bar">
                    <span class="search-icon">&#128269;</span>
                    <input v-model="search" @input="onSearch" placeholder="Search filename...">
                </div>
                <select class="sort-select" v-model="sort">
                    <option v-for="o in sortOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
                </select>
            </div>
        </div>

        <div v-if="loading && !files.length" class="loading"><div class="spinner"></div>Loading...</div>
        <div v-else-if="!files.length" class="empty">
            <div class="empty-icon">&#127902;</div>
            <div class="empty-text">No processed video files</div>
        </div>
        <div v-else>
            <div class="video-file-list">
                <div v-for="f in files" :key="f.id" class="video-file-card">
                    <div class="vf-filename">{{ f.filename }}</div>
                    <div class="vf-meta">
                        <span>{{ formatDate(f.start_time) }}</span>
                        <span class="sep">·</span>
                        <span>{{ duration(f.total_frames, f.fps) }}</span>
                        <span class="sep">·</span>
                        <span>{{ f.total_frames }} frames</span>
                        <span class="sep">·</span>
                        <span>{{ f.person_count }} person{{ f.person_count !== 1 ? 's' : '' }}</span>
                        <span class="sep">·</span>
                        <span>{{ f.track_count }} tracks</span>
                    </div>
                    <div class="vf-persons" v-if="f.persons && f.persons.length">
                        <div v-for="p in f.persons" :key="p.local_person_id" class="vf-person">
                            <div class="vf-person-thumbs">
                                <div class="vf-thumb-wrap" v-for="s in p.segments" :key="s.segment_id ?? s.face_track_id">
                                    <img :src="s.thumb_url"
                                         onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 64 64%22><rect fill=%22%231e293b%22 width=%2264%22 height=%2264%22/><text x=%2232%22 y=%2242%22 text-anchor=%22middle%22 fill=%22%23475569%22 font-size=%2228%22>&#128100;</text></svg>'"
                                         :title="(s.quality*100).toFixed(0)+'%'"
                                         @mouseenter="openPhotoTooltip($event, {thumb_url: s.thumb_url, filename: f.filename, frame_index: s.frame_index, total_frames: f.total_frames, fps: f.fps, start_time: f.start_time})"
                                         @mouseleave="closePhotoTooltip()"
                                         @click.stop="openFfPerson(p.local_person_id, f.id, s.face_track_id)">
                                </div>
                            </div>
                            <div class="vf-person-name"
                                 @click="openFfPerson(p.local_person_id, f.id)">
                                {{ p.immich_person_name || p.label }}
                            </div>
                        </div>
                    </div>
                    <div class="vf-no-persons" v-else>No faces detected</div>
                </div>
            </div>
            <div class="load-more" v-if="hasMore">
                <button class="btn btn-ghost" @click="load()" :disabled="loading">
                    {{ loading ? 'Loading...' : 'Load more' }}
                </button>
            </div>
        </div>
    </div>
    `,
});

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------
const router = createRouter({
    history: createWebHistory(),
    routes: [
        { path: '/',           redirect: '/videos' },
        { path: '/videos',     component: VideoFilesView },
        { path: '/ff/persons', component: FfPersonsView },
        { path: '/persons',    component: PersonsView },
        { path: '/persons/:id', component: PersonDetailView },
        { path: '/assets',     component: AssetsView },
        { path: '/assets/:id', component: AssetDetailView },
        { path: '/unassigned', component: UnassignedView },
        { path: '/log',        component: MergeLogView },
    ],
    scrollBehavior: () => ({ top: 0 }),
});

// ---------------------------------------------------------------------------
// Root App
// ---------------------------------------------------------------------------
const App = defineComponent({
    components: { RouterView, MergeModal, ToastContainer, FfPersonModal, PhotoModal },
    setup() {
        const route = useRoute();
        const stats = ref(null);
        const navOpen = ref(false);

        async function loadStats() {
            try {
                stats.value = await api.get('/api/stats');
            } catch {}
        }

        function onKeydown(e) {
            if (photoModal.show) {
                if (e.key === 'Escape')     { photoModal.show = false; return; }
                if (e.key === 'ArrowLeft')  { prevPhoto(); return; }
                if (e.key === 'ArrowRight') { nextPhoto(); return; }
                return;
            }
            if (e.key !== 'Escape') return;
            if (ffPersonModal.show) { ffPersonModal.show = false; return; }
            if (mergeState.show)    { closeMerge(); return; }
        }

        onMounted(() => { loadStats(); document.addEventListener('keydown', onKeydown); });
        onUnmounted(() => document.removeEventListener('keydown', onKeydown));

        function isActive(path) {
            return route.path === path || route.path.startsWith(path + '/');
        }

        return { stats, isActive, navOpen };
    },
    template: `
    <div class="sidebar">
        <div class="sidebar-logo">
            <div class="logo-title">Face Finder</div>
            <div class="logo-sub">Immich face management</div>
        </div>
        <div class="sidebar-stats" v-if="stats">
            <div class="stat-row">
                <span>Named persons</span>
                <span class="val">{{ stats.named_persons }}</span>
            </div>
            <div class="stat-row">
                <span>Total persons</span>
                <span class="val">{{ stats.total_persons }}</span>
            </div>
            <div class="stat-row">
                <span>Total faces</span>
                <span class="val">{{ stats.total_faces }}</span>
            </div>
            <div class="stat-row">
                <span>Assets w/ faces</span>
                <span class="val">{{ stats.assets_with_faces }}</span>
            </div>
        </div>
        <nav class="sidebar-nav">
            <div :class="['nav-link', { active: isActive('/videos') }]"
                 @click="$router.push('/videos')">
                <span class="nav-icon">&#127902;</span>
                Videos
            </div>
            <div :class="['nav-link', { active: isActive('/ff/persons') }]"
                 @click="$router.push('/ff/persons')">
                <span class="nav-icon">&#128100;</span>
                Persons
            </div>
            <div :class="['nav-link', { active: isActive('/assets') }]"
                 @click="$router.push('/assets')">
                <span class="nav-icon">&#128444;</span>
                Assets
            </div>
            <div :class="['nav-link', { active: isActive('/unassigned') }]"
                 @click="$router.push('/unassigned')">
                <span class="nav-icon">&#10067;</span>
                Unassigned
                <span class="nav-badge" v-if="stats && stats.unassigned_faces">{{ stats.unassigned_faces }}</span>
            </div>
            <div :class="['nav-link', { active: isActive('/log') }]"
                 @click="$router.push('/log')">
                <span class="nav-icon">&#128221;</span>
                Merge Log
            </div>
        </nav>
    </div>
    <div class="main-content">
        <RouterView />
    </div>
    <MergeModal />
    <FfPersonModal />
    <PhotoModal />
    <ToastContainer />
    `,
});

createApp(App).use(router).mount('#app');
