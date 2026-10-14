// A fresh canvas (first visit, "New", closing the last tab) opens the studio workflow
// instead of the frontend's built-in Z-Image graph, which needs a different text encoder type.
const WORKFLOW = 'workflows/lustify-krea2.json';
const { app } = window.comfyAPI.app;
const { api } = window.comfyAPI.api;
const { defaultGraph } = window.comfyAPI.defaultGraph;

app.registerExtension({
    name: 'studio.DefaultWorkflow',
    // Runs before the frontend restores or creates the first graph.
    async init() {
        const response = await api.getUserData(WORKFLOW);
        if (!response.ok) {
            console.warn(`studio: ${WORKFLOW} not found, keeping the stock default graph`);
            return;
        }
        const workflow = await response.json();
        for (const key of Object.keys(defaultGraph)) {
            delete defaultGraph[key];
        }
        Object.assign(defaultGraph, workflow);
    },
});
