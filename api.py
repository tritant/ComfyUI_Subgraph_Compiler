import server
from aiohttp import web
import inspect
import re
import os
import ast
from nodes import NODE_CLASS_MAPPINGS

# Helper pour nettoyer les noms de variables
def sanitize_title_for_variable(title):
    if not title: return "unnamed_node"
    sane = re.sub(r'[\s\-:\[\]]+', '_', title)
    sane = re.sub(r'[^\w_]', '', sane)
    if sane and sane[0].isdigit(): sane = '_' + sane
    return sane or "unnamed_node"

# La classe DependencyResolver (INCHANGÉE)
class DependencyResolver:
    def __init__(self):
        self.resolved_classes = {}
        self.collected_imports = set()
        self.collected_definitions = {}

    def get_source_from_node(self, node):
        return ast.get_source_segment(self.full_source_code, node)

    def resolve(self, initial_class_names):
        for class_name in initial_class_names:
            if class_name in self.resolved_classes:
                continue
            if class_name in NODE_CLASS_MAPPINGS:
                self._resolve_class(NODE_CLASS_MAPPINGS[class_name])
        
        # La classe retourne maintenant les imports bruts et les définitions
        return self.collected_imports, "\n\n".join(self.collected_definitions.values())

    def _resolve_class(self, cls):
        class_name = cls.__name__
        if class_name in self.resolved_classes:
            return
        self.resolved_classes[class_name] = True
        for base in cls.__bases__:
            if base.__name__ != 'object':
                self._resolve_class(base)
        try:
            source_file = inspect.getsourcefile(cls)
            if not source_file or not os.path.exists(source_file):
                self.collected_definitions[class_name] = inspect.getsource(cls)
                return
            with open(source_file, 'r', encoding='utf-8') as f:
                self.full_source_code = f.read()
            tree = ast.parse(self.full_source_code)
        except (TypeError, IOError, SyntaxError):
            try:
                self.collected_definitions[class_name] = inspect.getsource(cls)
            except (TypeError, IOError): pass
            return
        visitor = AstVisitor(cls, self.full_source_code)
        visitor.visit(tree)
        self.collected_imports.update(visitor.imports)
        if class_name not in self.collected_definitions and visitor.main_class_node:
            self.collected_definitions[class_name] = self.get_source_from_node(visitor.main_class_node)
        for dep_name, dep_node in visitor.dependencies.items():
            if dep_name not in self.collected_definitions:
                self.collected_definitions[dep_name] = self.get_source_from_node(dep_node)

# La classe AstVisitor (INCHANGÉE)
class AstVisitor(ast.NodeVisitor):
    def __init__(self, target_class, source_code):
        self.target_class = target_class
        self.target_class_name = target_class.__name__
        self.source_code = source_code
        self.imports = set()
        self.dependencies = {}
        self.potential_dependencies = {}
        self.main_class_node = None
        self.is_in_target_class = False
    def get_source_from_node(self, node):
        return ast.get_source_segment(self.source_code, node)
    def visit_Import(self, node):
        self.imports.add(self.get_source_from_node(node))
        self.generic_visit(node)
    def visit_ImportFrom(self, node):
        if node.level == 0: self.imports.add(self.get_source_from_node(node))
        self.generic_visit(node)
    def visit_ClassDef(self, node):
        if node.name == self.target_class_name:
            self.main_class_node = node
            self.is_in_target_class = True
            self.generic_visit(node)
            self.is_in_target_class = False
        else: self.potential_dependencies[node.name] = node
    def visit_FunctionDef(self, node):
        self.potential_dependencies[node.name] = node
        self.generic_visit(node)
    def visit_Name(self, node):
        if self.is_in_target_class and isinstance(node.ctx, ast.Load):
            if node.id in self.potential_dependencies:
                self.dependencies[node.id] = self.potential_dependencies[node.id]
        self.generic_visit(node)

# NOUVELLE FONCTION pour lire le code source des INPUT_TYPES
def get_input_type_str_from_source(node_class, input_name):
    try:
        source_code = inspect.getsource(node_class.INPUT_TYPES)
        # On nettoie le code pour l'analyser
        source_code = "\n".join(line for line in source_code.split('\n') if not line.strip().startswith('@'))
        
        tree = ast.parse(source_code)
        
        # On cherche le dictionnaire retourné dans la méthode
        for node in ast.walk(tree):
            if isinstance(node, ast.Return):
                if isinstance(node.value, ast.Dict):
                    # On parcourt les clés du dictionnaire ('required', 'optional')
                    for key_node in node.value.keys:
                        if isinstance(key_node, ast.Constant) and key_node.value == 'required':
                            required_dict_node = node.value.values[node.value.keys.index(key_node)]
                            if isinstance(required_dict_node, ast.Dict):
                                # On cherche notre input par son nom
                                for i, name_node in enumerate(required_dict_node.keys):
                                    if isinstance(name_node, ast.Constant) and name_node.value == input_name:
                                        # On a trouvé ! On récupère la définition du type
                                        type_def_tuple = required_dict_node.values[i]
                                        if isinstance(type_def_tuple, ast.Tuple) and type_def_tuple.elts:
                                            # On extrait le code source du premier élément (le type)
                                            return ast.get_source_segment(source_code, type_def_tuple.elts[0])
    except (TypeError, IOError, SyntaxError, IndexError):
        # En cas d'échec, on se rabat sur l'ancienne méthode
        pass
    
    # Fallback
    input_def = node_class.INPUT_TYPES().get('required', {}).get(input_name)
    if input_def:
        type_repr = repr(input_def[0])
        match = re.match(r"^<([^:]+).*", type_repr)
        return match.group(1) if match else type_repr
    return None


# La route get_node_source (INCHANGÉE)
async def get_node_source(request):
    class_name = request.rel_url.query.get('class_name', None)
    if not class_name or class_name not in NODE_CLASS_MAPPINGS:
        return web.json_response({"error": f"Classe '{class_name}' non trouvée."}, status=404)
    node_class = NODE_CLASS_MAPPINGS[class_name]
    try:
        source_code = inspect.getsource(node_class)
        source_file = inspect.getsourcefile(node_class)
        if not source_code: raise IOError("Le code source est vide.")
        return web.json_response({
            "class_name": class_name, "file_path": source_file,
            "source_code": source_code, "FUNCTION": node_class.FUNCTION
        })
    except (TypeError, IOError) as e:
        error_message = f"Impossible de lire le code source pour '{class_name}'. Raison : {e}"
        return web.json_response({"error": error_message}, status=500)

# Le générateur de code principal
async def generate_code_handler(request):
    data = await request.json()
    try:
        sane_class_name = sanitize_title_for_variable(data['newClassName'])

        initial_classes_to_process = {node['class_name'] for node in data['executionOrder']}
        resolver = DependencyResolver()
        collected_imports, definitions_code = resolver.resolve(initial_classes_to_process)
        
        # ... (La logique de détection des inputs reste inchangée)
        all_handled_inputs = set()
        for link in data['internalLinks']: all_handled_inputs.add(f"{link['target_id']}:{link['target_slot']}")
        for inp in data['ioMap']['inputs'].values(): all_handled_inputs.add(f"{inp['targetNodeId']}:{inp['targetNodeSlot']}")
        for node in data['executionOrder']:
            node_class = NODE_CLASS_MAPPINGS[node['class_name']]
            required_inputs = node_class.INPUT_TYPES().get('required', {})
            for i, input_slot_info in enumerate(node['inputs']):
                input_name = input_slot_info['name']
                if input_name in required_inputs and f"{node['id']}:{i}" not in all_handled_inputs:
                    new_input_name = f"{sanitize_title_for_variable(node['title'])}_{input_name}"
                    data['ioMap']['inputs'][new_input_name] = {'name': new_input_name, 'type': input_slot_info['type'], 'originalClassName': node['class_name'], 'originalInputName': input_name, 'targetNodeId': node['id'], 'targetNodeSlot': i}

      # --- MODIFICATION ULTIME DE LA LOGIQUE D'ASSEMBLAGE DES IMPORTS ---
        
        base_imports = {
            "import torch", "import folder_paths", "import comfy.sd",
            "from comfy import utils", "from enum import StrEnum"
        }
        
        all_imports = base_imports.union(collected_imports)
        
        # On sépare en deux listes triées
        future_imports = sorted([imp for imp in all_imports if imp.startswith('from __future__')])
        other_imports = sorted([imp for imp in all_imports if not imp.startswith('from __future__')])
        
        # On combine les listes et on fait un seul join
        final_import_code = "\n".join(future_imports + other_imports)

        # --- FIN DE LA MODIFICATION ---

        # Construction du fichier final
        code = "# Fichier généré par le Subgraph Compiler\n"
        code += final_import_code + "\n\n"
        
        code += "# --- Définitions des classes de nœuds internes et de leurs dépendances ---\n"
        code += definitions_code + "\n\n"

        code += f"# --- Définition du nouveau nœud compilé ---\n"
        # ... (Le reste de la fonction pour construire la classe, les inputs, l'execute, etc. est INCHANGÉ)
        code += f"class {sane_class_name}:\n"
        code += f"    @classmethod\n    def INPUT_TYPES(s):\n        return {{ 'required': {{\n"
        for name, details in data['ioMap']['inputs'].items():
            original_class_name, original_input_name = details.get('originalClassName', ''), details.get('originalInputName', '')
            if original_class_name and original_input_name and original_class_name in NODE_CLASS_MAPPINGS:
                node_class = NODE_CLASS_MAPPINGS[original_class_name]
                input_def = node_class.INPUT_TYPES().get('required', {}).get(original_input_name)
                if input_def:
                   
# MODIFICATION : On utilise notre nouvelle fonction AST pour récupérer le type
                    type_str = get_input_type_str_from_source(node_class, original_input_name)
                    if type_str is None: # Fallback au cas où l'analyse AST échoue
                         type_repr, match = repr(input_def[0]), re.match(r"^<([^:]+).*", repr(input_def[0]))
                         type_str = match.group(1) if match else type_repr
                    # FIN DE LA MODIFICATION
                    if len(input_def) > 1: code += f'            "{name}": ({type_str}, {repr(input_def[1])}),\n'
                    else: code += f'            "{name}": ({type_str},),\n'
                else: code += f'            "{name}": ("{details["type"]}",),\n'
            else: code += f'            "{name}": ("{details["type"]}",),\n'
        code += f'        }} }}\n\n'
        code += f"    RETURN_TYPES = ({', '.join([f'\"{o["type"]}\"' for o in data['ioMap']['outputs'].values()])},)\n"
        code += f"    RETURN_NAMES = ({', '.join([f'\"{n["name"]}\"' for n in data['ioMap']['outputs'].values()])},)\n"
        code += f"    FUNCTION = \"execute\"\n    CATEGORY = \"{data['newCategory']}\"\n\n"
        code += f"    def execute(self, {', '.join(list(data['ioMap']['inputs'].keys()))}):\n"
        output_vars = {}
        for node in data['executionOrder']:
            instance_name, node_class_name = f"{sanitize_title_for_variable(node['title'])}_{node['id']}", node['class_name']
            node_class = NODE_CLASS_MAPPINGS[node_class_name]
            function_name = node_class.FUNCTION
            code += f"\n        # Exécution de {node['title']}\n"
            code += f"        {instance_name} = {node_class_name}()\n"



            args = {}
            node_info = next((n for n in data['executionOrder'] if n['id'] == node['id']), None)

            # 1. Gérer les connexions internes du subgraph
            internal_links_for_node = [l for l in data['internalLinks'] if l['target_id'] == node['id']]
            for link in internal_links_for_node:
                if node_info and link['target_slot'] < len(node_info['inputs']):
                    # On trouve le nom de l'argument grâce à la fente (slot) d'entrée
                    arg_name = node_info['inputs'][link['target_slot']]['name']
                    origin_node_id, origin_slot = link['origin_id'], link['origin_slot']
                    if origin_node_id in output_vars and origin_slot < len(output_vars[origin_node_id]):
                        args[arg_name] = output_vars[origin_node_id][origin_slot]

            # 2. Gérer les entrées exposées du subgraph
            exposed_inputs_for_node = [inp for inp in data['ioMap']['inputs'].values() if inp['targetNodeId'] == node['id']]
            for inp in exposed_inputs_for_node:
                 if node_info and inp['targetNodeSlot'] < len(node_info['inputs']):
                    # On trouve le nom de l'argument d'origine
                    original_arg_name = node_info['inputs'][inp['targetNodeSlot']]['name']
                    # On l'assigne à la variable d'entrée du nouveau noeud
                    args[original_arg_name] = inp['name']
            
            # --- FIN DE LA MODIFICATION ---
           


            args_str = "".join([f"            {k}={v},\n" for k, v in args.items()])
            return_vars = [f"out_{node['id']}_{i}" for i in range(len(node['outputs']))]
            output_vars[node['id']] = return_vars
            if return_vars: code += f"        ({', '.join(return_vars)},) = {instance_name}.{function_name}(\n{args_str}        )\n"
            else: code += f"        {instance_name}.{function_name}(\n{args_str}        )\n"
        final_return_vars = [output_vars[out['originNodeId']][out['originNodeSlot']] for out in data['ioMap']['outputs'].values() if out['originNodeId'] in output_vars]
        code += f"\n        return ({', '.join(final_return_vars)},)\n"
        code += f"\n# --- Mappings pour ComfyUI ---\n"
        code += f"NODE_CLASS_MAPPINGS = {{ \"{sane_class_name}\": {sane_class_name} }}\n"
        code += f"NODE_DISPLAY_NAME_MAPPINGS = {{ \"{sane_class_name}\": \"{data['newClassName']}\" }}\n"
        
        return web.Response(text=code, content_type='text/plain')
    except Exception as e:
        import traceback
        return web.Response(status=500, text=f"Erreur lors de la génération du code: {e}\n{traceback.format_exc()}")

def add_api_routes(app):
    print("✅ Ajout des routes API pour le Subgraph Compiler...")
    app.add_routes([
        web.get('/subgraph_compiler/get_node_source', get_node_source),
        web.post('/subgraph_compiler/generate_code', generate_code_handler)
    ])