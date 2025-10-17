class SubgraphCompiler:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
            "generated_code": ("STRING", {"default": "# Le code apparaîtra ici", "multiline": True}),
            },
            # On déclare une entrée "cachée" de type CABLES
            # Cela nous permettra de connecter n'importe quel nœud dessus
            "hidden": {"subgraph_ref": ("CABLES",)},
        }

    RETURN_TYPES = ()
    FUNCTION = "do_nothing"
    OUTPUT_NODE = True # Ce nœud ne produit pas de sortie pour le workflow
    CATEGORY = "z_Tools/Experimental" # La catégorie où trouver le nœud

    def do_nothing(self, **kwargs):
        # Ce nœud ne fait rien côté Python, tout est géré en JavaScript.
        return {}
