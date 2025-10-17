import { app } from "/scripts/app.js";

function isNodeSubgraph(node) {
    if (!node || !node.type || typeof node.type !== 'string') return false;
    const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    return uuidRegex.test(node.type);
}

function analyzeSubgraph(subgraphNode) {
    const internalGraph = subgraphNode.subgraph;
    if (!internalGraph) return null;

    const nodes = Object.values(internalGraph._nodes_by_id || {});
    const links = Array.from((internalGraph.links || new Map()).values());
    const nodeIds = new Set(nodes.map(n => n.id));

    const ioMap = { inputs: {}, outputs: {} };

    const inputLinks = links.filter(l => l && !nodeIds.has(l.origin_id));
    for (const link of inputLinks) {
        const targetNode = internalGraph.getNodeById(link.target_id);
        const inputOnParent = subgraphNode.inputs[link.origin_slot];
        if (targetNode && inputOnParent) {
            const originalInputSlot = targetNode.inputs[link.target_slot];
            ioMap.inputs[inputOnParent.name] = { 
                name: inputOnParent.name, 
                type: inputOnParent.type,
                targetNodeId: link.target_id, 
                targetNodeSlot: link.target_slot,
                originalClassName: LiteGraph.getNodeType(targetNode.type).nodeData.name,
                originalInputName: originalInputSlot.name,
            };
        }
    }
    
    const outputLinks = links.filter(l => l && !nodeIds.has(l.target_id));
    for (const link of outputLinks) {
        const originNode = internalGraph.getNodeById(link.origin_id);
        const outputOnParent = subgraphNode.outputs[link.target_slot];
        if(originNode && outputOnParent) {
            ioMap.outputs[outputOnParent.name] = { 
                name: outputOnParent.name, 
                type: outputOnParent.type, 
                originNodeId: link.origin_id, 
                originNodeSlot: link.origin_slot,
            };
        }
    }

    const executionOrder = [];
    const internalLinks = links.filter(l => l && nodeIds.has(l.origin_id) && nodeIds.has(l.target_id));
    const inDegree = new Map(nodes.map(n => [n.id, 0]));
    for (const link of internalLinks) {
        inDegree.set(link.target_id, inDegree.get(link.target_id) + 1);
    }
    
    const queue = nodes.filter(n => inDegree.get(n.id) === 0);
    while (queue.length > 0) {
        const node = queue.shift();
        executionOrder.push(node);
        const outgoingLinks = internalLinks.filter(l => l && l.origin_id === node.id);
        for (const link of outgoingLinks) {
            inDegree.set(link.target_id, inDegree.get(link.target_id) - 1);
            if (inDegree.get(link.target_id) === 0) {
                const nextNode = internalGraph.getNodeById(link.target_id);
                if (nextNode) queue.push(nextNode);
            }
        }
    }
    return { ioMap, executionOrder: executionOrder.filter(n => n.type !== "GraphInput" && n.type !== "GraphOutput") };
}

app.registerExtension({
    name: "Comfy.SubgraphCompiler",
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name === "SubgraphCompiler") {
            const onNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                onNodeCreated?.apply(this, arguments);
                this.addInput("(Subgraph Reference)", "*");
                const classNameWidget = this.addWidget("STRING", "New Node Class Name", "MySuperNode", {});
                const categoryWidget = this.addWidget("STRING", "New Node Category", "_my_nodes/custom", {});
                const statusWidget = this.addWidget("STRING", "Status", "Ready", {});
                const codeWidget = this.widgets.find(w => w.name === "generated_code");
                
 this.addWidget("button", "Compile Subgraph", null, async () => {
                    statusWidget.value = "Analyse en cours...";
                    await new Promise(r => setTimeout(r, 0));

                    let connectedSubgraphNode = null;
                    const input = this.inputs.find(i => i.name === "(Subgraph Reference)"); 
                    if (input && input.link) {
                        connectedSubgraphNode = app.graph.getNodeById(app.graph.links[input.link].origin_id);
                    }

                    if (!isNodeSubgraph(connectedSubgraphNode)) {
                        statusWidget.value = "Erreur : Aucun Subgraph valide n'est connecté.";
                        return;
                    }

                    const analysis = analyzeSubgraph(connectedSubgraphNode);
                    if(!analysis || !analysis.executionOrder) {
                        statusWidget.value = "Erreur : L'analyse du graphe a échoué.";
                        return;
                    }
                    
                    statusWidget.value = `Récupération du code source (${analysis.executionOrder.length} nœuds)...`;
                    await new Promise(r => setTimeout(r, 0));
                    
                    const nodeSources = {};
                    for (const node of analysis.executionOrder) {
                        try {
                            const nodeTypeName = LiteGraph.getNodeType(node.type).nodeData.name;
                            const response = await fetch(`/subgraph_compiler/get_node_source?class_name=${nodeTypeName}`);
                            if (!response.ok) { throw new Error(`Erreur HTTP ${response.status}`); }
                            const data = await response.json();
                             if (data.error) { throw new Error(data.error); }
                            nodeSources[node.id] = data;
                        } catch(e) {
                            statusWidget.value = `Erreur API pour ${node.title}: ${e.message}`;
                            return;
                        }
                    }

                    statusWidget.value = "Génération du code par le backend...";
                    
                    // --- CORRECTION : Nettoyage des objets avant l'envoi ---
                     const sanitizedExecutionOrder = analysis.executionOrder.map(node => ({
                        id: node.id,
                        title: node.title,
                        type: node.type,
                        class_name: LiteGraph.getNodeType(node.type).nodeData.name,
                        inputs: node.inputs.map(i => ({ name: i.name, type: i.type })),
                        outputs: node.outputs.map(o => ({ name: o.name, type: o.type })),
                    }));

                    const sanitizedLinks = Array.from(connectedSubgraphNode.subgraph.links.values()).map(l => ({
                        id: l.id,
                        origin_id: l.origin_id,
                        origin_slot: l.origin_slot,
                        target_id: l.target_id,
                        target_slot: l.target_slot,
                    }));

                    const payload = {
                        newClassName: classNameWidget.value,
                        newCategory: categoryWidget.value,
                        ioMap: analysis.ioMap,
                        executionOrder: sanitizedExecutionOrder, // On envoie la version nettoyée
                        internalLinks: sanitizedLinks, // On envoie la version nettoyée
                        nodeSources: nodeSources,
                    };
                    // --- FIN DE LA CORRECTION ---
                    
                    const genResponse = await fetch('/subgraph_compiler/generate_code', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    
                    if (!genResponse.ok) {
                        statusWidget.value = `Erreur Backend: ${await genResponse.text()}`;
                        return;
                    }
                    
                    const finalCode = await genResponse.text();
                    codeWidget.value = finalCode;
                    statusWidget.value = "✅ Succès ! Code généré.";

                    setTimeout(() => { this.computeSize(); app.graph.setDirtyCanvas(true, true); }, 0);
                });
                
                this.addWidget("button", "Copy to Clipboard", null, () => {
                    if (codeWidget.value) {
                        navigator.clipboard.writeText(codeWidget.value).then(() => {
                            statusWidget.value = "Copié dans le presse-papiers !";
                        }, () => { statusWidget.value = "Erreur lors de la copie."; });
                    }
                });
            };
        }
    },
});