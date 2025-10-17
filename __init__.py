import server
from .api import add_api_routes

# On importe la classe de notre nouveau nœud depuis son fichier
from .compiler_node import SubgraphCompiler

# Le serveur est prêt, on ajoute les routes
add_api_routes(server.PromptServer.instance.app)

# On déclare à ComfyUI le nom de la classe et le nom à afficher dans le menu
NODE_CLASS_MAPPINGS = {
    "SubgraphCompiler": SubgraphCompiler
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "SubgraphCompiler": "Subgraph Compiler"
}

# On indique à ComfyUI où se trouve notre code JavaScript
WEB_DIRECTORY = "./js"

print("✅ Pack Subgraph Compiler chargé.")
