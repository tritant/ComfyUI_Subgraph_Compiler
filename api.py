import server
from aiohttp import web
import inspect
import re
import os
import ast
from nodes import NODE_CLASS_MAPPINGS
import folder_paths
import types
import json
import sys
import traceback
from collections import defaultdict
import builtins
import importlib.util
import graphlib
import pprint
import warnings
# ===============================================================
# --- CONSTANTES DE CONFIGURATION ---
# ===============================================================
EXTERNAL_MODULES = {
    'torch', 'numpy', 'PIL', 'comfy', 'folder_paths', 'latent_preview', 
    'requests', 'einops', 'safetensors', 'spandrel', 'timm', 'transformers', 
    'diffusers', 'scipy', 'skimage', 'comfy_api', 'git', 'gitdb', 
    'setuptools', 'typing', 'typing_extensions', 'mediapipe'
}
UNIX_ONLY_MODULES = {'fcntl', 'grp', 'pwd', 'resource', 'termios'}
LEGACY_PY2_MODULES = {'StringIO', 'cStringIO', 'dummy_threading'}
IGNORE_IMPORTS = {'brotli', 'brotlicffi', 'amdsmi', 'io', 'mediapipe'}
IGNORE_DEFINITIONS = {
    'cached_func',
    'attention_bleh',
    'attn',
    'FeedForward',
    'apply_rotary_emb'
}

# ===============================================================
# --- INDEXEUR DE CLASSES ---
# ===============================================================
CLASS_INDEX = None
FUNCTION_INDEX = None

def build_indexes():
    global CLASS_INDEX, FUNCTION_INDEX
    if CLASS_INDEX is not None and FUNCTION_INDEX is not None:
        return

    print("--- Subgraph Compiler: Building final indexes... ---")
    CLASS_INDEX = {}
    FUNCTION_INDEX = {}
    
    for class_name, class_obj in NODE_CLASS_MAPPINGS.items():
        if hasattr(class_obj, '__module__'):
            CLASS_INDEX[class_name] = class_obj.__module__

    comfy_root = os.path.dirname(folder_paths.__file__)
    
    paths_to_scan = folder_paths.get_folder_paths("custom_nodes")
    comfy_extras_path = os.path.join(comfy_root, "comfy_extras")
    if os.path.isdir(comfy_extras_path):
        paths_to_scan.append(comfy_extras_path)

    base_dir_for_paths = comfy_root
    scanned_files = set()
    
    def _get_tag_from_path(file_path, base_path):
        # Helper pour extraire un tag propre depuis le chemin du fichier
        rel_path = os.path.relpath(os.path.dirname(file_path), base_path)
        return rel_path.replace(os.sep, '_').replace('-', '_')

    for scan_path in paths_to_scan:
        if not os.path.isdir(scan_path):
            continue
        for root, _, files in os.walk(scan_path):
            if "venv" in root or ".git" in root:
                continue
            for file in files:
                if file.endswith(".py"):
                    file_path = os.path.join(root, file)

                    # --- LOG EN ROUGE RÉINTÉGRÉ ---
                    if "nodes_custom_sampler.py" in file_path:
                        print("\033[91m" + f"\n>>> DÉTECTÉ : Analyse du fichier critique : {file_path}" + "\033[0m")
                    
                    if file_path in scanned_files:
                        continue
                    scanned_files.add(file_path)

                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                        
                            # ▼▼▼ AJOUT DE LA VÉRIFICATION DU TAG ▼▼▼
                            first_line = f.readline().strip()
                            if first_line == "# ---Don't use this file for build_indexes---":
                                print(f"  -> Ignoré (fichier venant du compilateur): {file_path}")
                                continue # Passe au fichier suivant
                            # ▲▲▲ FIN DE L'AJOUT ▲▲▲

                            # Si on est ici, ce n'est pas un fichier généré, on lit le reste
                            f.seek(0) # Revenir au début du fichier
                        
                            source_code = f.read()
                        
                        # ▼▼▼ AJOUT DU FILTRE D'AVERTISSEMENTS ▼▼▼
                        with warnings.catch_warnings():
                            # Ignorer spécifiquement les SyntaxWarning pendant l'analyse AST
                            warnings.filterwarnings("ignore", category=SyntaxWarning)
                            # L'appel ast.parse() est maintenant à l'intérieur du contexte
                            tree = ast.parse(source_code)
                        # ▲▲▲ FIN DE L'AJOUT ▲▲▲
                                                
                        rel_path = os.path.relpath(file_path, base_dir_for_paths)
                        module_path = os.path.splitext(rel_path)[0].replace(os.sep, '.')
                        


                        for node in ast.walk(tree):
                            if isinstance(node, ast.ClassDef):
                                class_name = node.name
                                if class_name not in CLASS_INDEX:
                                    # Cas normal : première fois qu'on voit ce nom
                                    CLASS_INDEX[class_name] = module_path
                                else:
                                 if class_name in ['NunchakuQwenImage', 'Attention']:
                                    print(f"⚠️  Doublon détecté pour la classe '{class_name}'. Ajout d'une nouvelle version depuis '{module_path}'.")
                                    # On a une collision !
                                    existing_entry = CLASS_INDEX[class_name]
                                    new_entry = {'path': module_path, 'tag': _get_tag_from_path(file_path, base_dir_for_paths)}

                                    if isinstance(existing_entry, str):
                                        # Première collision pour ce nom. On transforme l'entrée existante.
                                        old_path = existing_entry
                                        # Pour trouver le fichier original de l'entrée existante, on doit faire un peu de travail
                                        old_file_path = old_path.replace('.', os.sep) + '.py'
                                        full_old_path = os.path.join(base_dir_for_paths, old_file_path)
                                      
                                        old_tag = _get_tag_from_path(full_old_path, base_dir_for_paths)
                                        CLASS_INDEX[class_name] = [
                                            {'path': old_path, 'tag': old_tag},
                                            new_entry
                                        ]
                                    elif isinstance(existing_entry, list):
                                        # Il y avait déjà des collisions, on ajoute à la liste.
                                        CLASS_INDEX[class_name].append(new_entry)
                                 else:
                                      pass
                            elif isinstance(node, ast.FunctionDef):
                                # On garde la logique simple pour les fonctions pour l'instant
                                if node.name not in FUNCTION_INDEX:
                                    FUNCTION_INDEX[node.name] = module_path
                        # ▲▲▲ FIN DE LA MODIFICATION ▲▲▲
                    except Exception:
                        continue

    print(f"--- Subgraph Compiler: Indexes built. Found {len(CLASS_INDEX)} classes and {len(FUNCTION_INDEX)} functions. ---")

# ===============================================================
# --- COMPILATEUR MINIMALISTE ("ZEN") ---
# ===============================================================
class DependencyResolver:
    def __init__(self, node_class_mappings):
        self.node_class_mappings = node_class_mappings
        self.class_index = CLASS_INDEX
        self.function_index = FUNCTION_INDEX
        self.rename_map = {} # Pour suivre les renommages
        self.dependency_graph = {}
        
    def resolve(self, initial_class_names):
        print("\n--- DÉMARRAGE DE LA RÉSOLUTION (Finale + Tri Topologique) ---")
        
        # La liste IGNORE_DEPS (sans sd, metadata, state_dict)
        IGNORE_DEPS = {
            '_', 'self', 'cls', 'i', 'x', 'f', 'k', 'v', 's', 't', 'e', 'ret',
            'args', 'kwargs', 'name', 'path', 'shape', 'next', 'CATEGORY', 'filter',
            'common_ksampler', 'sampler', 'noise', 'text', 'total', 'round', 'rank', 'h', 'add', 'max',
            'index', 'in_channels', 'control', 'out_channels', 'scale', 'dim', 'shift', 'warn', 'block_wrap',
            'node', 'model', 'clip', 'image', 'images', 'vae', 'seed', 'steps', 'cfg', 'sampler_name', 
            'scheduler', 'positive', 'negative', 'latent', 'latent_image', 'denoise', 'prompt', 'pixels', 
            'samples', 'conditioning', 'callback', 'dtype', 'device', 'self_attn', 'width', 'height', 'batch_size', 'percent',
            'int', 'str', 'float', 'bool', 'list', 'dict', 'tuple', 'set', 'any', 'Any',
            'None', 'True', 'False', 'res', 'sample', 'step', 'prepare_noise', 'sigma', 'timestep'
        }

        stack = list(initial_class_names)
        processed_names = set(dir(__builtins__))
        all_code_blocks = {}
        all_imports = set()
        self.dependency_graph = {}

        while stack:
            name = stack.pop()
            if name in processed_names:
                continue

            if name in IGNORE_DEFINITIONS:
                continue
            
            # Si on a déjà traité des versions renommées de ce nom, on l'ignore
            if any(p.startswith(name + '_') for p in processed_names):
                processed_names.add(name)
                continue

            lookup_result = self.class_index.get(name) or self.function_index.get(name)
            if not lookup_result:
                print(f"  -> AVERTISSEMENT: Nom '{name}' non trouvé dans les index. Ignoré.")
                processed_names.add(name)
                continue

            entries_to_process = lookup_result if isinstance(lookup_result, list) else [{'path': lookup_result, 'tag': None}]
            
            # ▼▼▼ MODIFICATION DE LA LOGIQUE DE RENOMMAGE ▼▼▼
            for entry in entries_to_process:
                module_path = entry['path']
                tag = entry['tag']

                is_duplicate_entry = len(entries_to_process) > 1
                
                # Règle de renommage déterministe basée sur le tag
                # La classe 'model_base' est la principale, elle n'est JAMAIS renommée.
                # Toutes les autres versions (comme 'model_configs') SONT renommées.
                is_main_version = tag and 'model_base' in tag
                should_rename = is_duplicate_entry and not is_main_version
                
                final_name = f"{name}_{tag}" if should_rename else name
                # ▲▲▲ FIN DE LA MODIFICATION ▲▲▲
                
                if final_name in processed_names:
                    continue

                print(f"\n[TÂCHE] Traitement de : '{name}' (tag: {tag or 'default'})")
                
                source_file = None
                try:
                    spec = importlib.util.find_spec(module_path)
                    if spec and spec.origin and spec.origin not in ['built-in', 'frozen']:
                        source_file = spec.origin
                except Exception: pass

                if not source_file or not source_file.endswith('.py'):
                    processed_names.add(final_name)
                    continue

                processed_names.add(final_name)
                if not should_rename and is_duplicate_entry:
                    processed_names.add(name)

                print(f"  -> Fichier source : {source_file}")

                try:
                    with open(source_file, 'r', encoding='utf-8') as f:
                        source_code = f.read()

                    file_tree = ast.parse(source_code)
                    definitions_in_file = {node.name: node for node in ast.walk(file_tree) if isinstance(node, (ast.FunctionDef, ast.ClassDef))}

                    target_node = definitions_in_file.get(name)
                    if target_node:
                        code_segment = ast.get_source_segment(source_code, target_node)
                        
                        if should_rename:
                            print(f"  -> Collision détectée. Renommage : '{name}' -> '{final_name}'")
                            pattern = r"(\bclass\s+|\bdef\s+)" + re.escape(name) + r"(\b)"
                            code_segment = re.sub(pattern, rf"\1{final_name}\2", code_segment, count=1)
                            self.rename_map[f"{name}_{tag}"] = final_name

                        if final_name in all_code_blocks: continue
                        all_code_blocks[final_name] = code_segment
                        print(f"  -> Code pour '{final_name}' collecté.")

                        for node in ast.walk(file_tree):
                            if isinstance(node, ast.ImportFrom) and node.level == 0: all_imports.add(ast.unparse(node))
                            elif isinstance(node, ast.Import): all_imports.add(ast.unparse(node))
                        
                        code_segment = code_segment.replace('model_base.NunchakuQwenImage', 'NunchakuQwenImage')
                        print(f"  -> Analyse des dépendances pour '{final_name}'...")
                        segment_tree = ast.parse(code_segment)
                        dependencies_found = []
                        self.dependency_graph.setdefault(final_name, set())
                        for node in ast.walk(segment_tree):
                            if isinstance(node, ast.Name):
                                dep_name = node.id

                                if dep_name in IGNORE_DEPS:
                                    continue

                                is_a_dependency = dep_name in definitions_in_file or dep_name in self.class_index or dep_name in self.function_index
                                
                                if is_a_dependency:
                                    # Log de débogage pour voir ce qu'il se passe
                                    print(f"    -> Dépendance potentielle identifiée: '{dep_name}'")
                                    
                                    # La condition la plus simple possible :
                                    if dep_name != final_name:
                                        print(f"      -> ✅ Ajout de la dépendance: '{final_name}' -> '{dep_name}'")
                                        self.dependency_graph[final_name].add(dep_name)
                                    else:
                                        print(f"      -> ❌ Ignoré (auto-dépendance): '{dep_name}'")

                                    if dep_name not in processed_names and dep_name not in stack:
                                        dependencies_found.append(dep_name)
                        
                        if dependencies_found:
                            print(f"  -> Dépendances découvertes : {list(set(dependencies_found))}")
                            stack.extend(list(set(dependencies_found)))
                        else:
                            print("  -> Fin de cette branche de dépendances.")

                except Exception as e:
                    print(f"  -> ERREUR lors de l'analyse de '{name}': {e}")
                    traceback.print_exc()
                    continue
        
        # ▼▼▼ AJOUT DU BLOC DE DÉBOGAGE ▼▼▼
        print("\n" + "="*50)
        print("--- GRAPHE DE DÉPENDANCES FINAL (AVANT TRI) ---")
        pprint.pprint(self.dependency_graph)
        print("="*50 + "\n")
        # ▲▲▲ FIN DU BLOC DE DÉBOGAGE ▲▲▲
        
        
        print("\n--- RÉSOLUTION TERMINÉE, DÉBUT DU TRI TOPOLOGIQUE ---")
        try:
            ts = graphlib.TopologicalSorter(self.dependency_graph)
            sorted_order = list(ts.static_order())
            print(f"Ordre de définition corrigé : {sorted_order}")
            
            sorted_code_blocks = [all_code_blocks[name] for name in sorted_order if name in all_code_blocks]
            final_bundle_code = "\n\n".join(sorted_code_blocks)
        except graphlib.CycleError as e:
            print(f"  -> ERREUR: Dépendance circulaire détectée: {e}. Utilisation de l'ordre par défaut.")
            final_bundle_code = "\n\n".join(all_code_blocks.values())
        
        final_imports = set()
        try:
            final_tree = ast.parse(final_bundle_code)
            used_names = {node.id for node in ast.walk(final_tree) if isinstance(node, ast.Name)}
            for imp_line in all_imports:
                try:
                    imp_node = ast.parse(imp_line).body[0]
                    module_name = ""
                    if isinstance(imp_node, ast.Import):
                        module_name = imp_node.names[0].name
                    elif isinstance(imp_node, ast.ImportFrom):
                        module_name = imp_node.module
                    
                    if module_name in IGNORE_IMPORTS:
                        continue

                    keep_import = False
                    if isinstance(imp_node, ast.Import):
                        for alias in imp_node.names:
                            if (alias.asname or alias.name).split('.')[0] in used_names:
                                keep_import = True; break
                    elif isinstance(imp_node, ast.ImportFrom):
                        if imp_node.module and imp_node.module.split('.')[0] in used_names:
                            keep_import = True
                        else:
                            for alias in imp_node.names:
                                if (alias.asname or alias.name) in used_names:
                                    keep_import = True; break
                    if keep_import:
                        final_imports.add(imp_line)
                except Exception: pass
        except Exception: pass

        return final_imports, final_bundle_code
        
def get_dynamic_input_str_from_source(class_name, input_name):
    """
    Analyse le code source d'une classe pour extraire la définition d'un input
    sous forme de chaîne de caractères, sans l'évaluer.
    Cible les appels de fonction (ast.Call) et les accès à des attributs (ast.Attribute).
    """
    if not class_name or not input_name or not CLASS_INDEX:
        return None

    module_path = CLASS_INDEX.get(class_name)
    if not module_path:
        return None

    source_file = None
    try:
        spec = importlib.util.find_spec(module_path)
        if spec and spec.origin and spec.origin not in ['built-in', 'frozen']:
            source_file = spec.origin
    except Exception:
        return None

    if not source_file or not source_file.endswith('.py'):
        return None

    try:
        with open(source_file, 'r', encoding='utf-8') as f:
            source_code = f.read()
        
        tree = ast.parse(source_code)
        
        class_node = next((n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == class_name), None)
        if not class_node: return None

        input_types_method = next((n for n in class_node.body if isinstance(n, ast.FunctionDef) and n.name == 'INPUT_TYPES'), None)
        if not input_types_method: return None # Cas d'héritage, on laisse le fallback gérer
            
        return_node = next((n for n in reversed(input_types_method.body) if isinstance(n, ast.Return)), None)
        if not return_node or not isinstance(return_node.value, ast.Dict): return None
            
        required_dict_node = None
        for i, key_node in enumerate(return_node.value.keys):
            key_val = getattr(key_node, 'value', getattr(key_node, 's', None))
            if key_val == 'required':
                required_dict_node = return_node.value.values[i]
                break
        
        if not required_dict_node or not isinstance(required_dict_node, ast.Dict): return None

        input_tuple_node = None
        for i, key_node in enumerate(required_dict_node.keys):
            key_val = getattr(key_node, 'value', getattr(key_node, 's', None))
            if key_val == input_name:
                input_tuple_node = required_dict_node.values[i]
                break
        
        if not input_tuple_node or not isinstance(input_tuple_node, ast.Tuple): return None

        type_definition_node = input_tuple_node.elts[0]
        
        # ▼▼▼ LA SEULE LIGNE QUI CHANGE ▼▼▼
        # On accepte maintenant les appels (Call) ET les attributs (Attribute) comme dynamiques.
        if isinstance(type_definition_node, (ast.Call, ast.Attribute)):
            return ast.unparse(type_definition_node)

    except Exception:
        return None # En cas d'erreur, on abandonne et on laisse faire le fallback
        
    return None

# ===============================================================
# --- FONCTIONS UTILITAIRES ET HANDLERS API ---
# ===============================================================

def sanitize_title_for_variable(title):
    if not title: return "unnamed_node"
    sane = re.sub(r'[\s\-:\[\]]+', '_', title)
    sane = re.sub(r'[^\w_]', '', sane)
    if sane and sane[0].isdigit(): sane = '_' + sane
    return sane or "unnamed_node"

async def get_node_source(request):
    class_name = request.rel_url.query.get('class_name', None)
    if not class_name or class_name not in NODE_CLASS_MAPPINGS:
        return web.json_response({"error": f"Classe '{class_name}' non trouvée."}, status=404)
    try:
        source_code = inspect.getsource(NODE_CLASS_MAPPINGS[class_name])
        return web.json_response({"source_code": source_code})
    except:
        return web.json_response({"error": f"Impossible de lire le code source pour '{class_name}'."}, status=500)

def _find_entry_points_from_execute(naive_code_body, resolver):
    """Analyse le code de la méthode execute pour trouver les classes instanciées."""
    entry_points = set()
    try:
        body_tree = ast.parse(naive_code_body)
        for node in ast.walk(body_tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                class_name_called = node.func.id
                # Vérifier si c'est une classe connue par le resolver
                lookup = resolver.class_index.get(class_name_called) or resolver.function_index.get(class_name_called)
                if lookup:
                    if isinstance(lookup, list): # C'est un doublon
                        # On ajoute le nom original ET les versions renommées qui existent
                        entry_points.add(class_name_called) # Ajouter le nom original (pour le graphe)
                        for entry in lookup:
                            tag = entry['tag']
                            renamed = f"{class_name_called}_{tag}"
                            if renamed in resolver.rename_map.values():
                                entry_points.add(renamed) # Ajouter la version renommée si elle a été copiée
                    else: # Pas un doublon
                        entry_points.add(class_name_called)

        print(f"  -> Points d'entrée détectés depuis execute: {entry_points}")
        return list(entry_points)
    except Exception as e:
        print(f"  -> ERREUR lors de la détection des points d'entrée: {e}. Élagage annulé.")
        return []
        
def build_final_dependency_graph(final_code):
    """
    Analyse le code final généré (après renommage et patchs)
    pour construire un graphe de dépendances précis.
    """
    print("--- Construction du graphe de dépendances final ---")
    final_graph = {}
    try:
        tree = ast.parse(final_code)
        # Identifier toutes les définitions présentes dans le code final
        all_final_definitions = {node.name for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))}
        print(f"  -> Trouvé {len(all_final_definitions)} définitions dans le code final.")

        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                current_name = node.name
                final_graph[current_name] = set()
                
                # Explorer les noms utilisés à l'intérieur de cette définition
                for sub_node in ast.walk(node):
                    if isinstance(sub_node, ast.Name):
                        dep_name = sub_node.id
                        # Si le nom utilisé est une autre définition DANS NOTRE CODE FINAL
                        if dep_name != current_name and dep_name in all_final_definitions:
                            final_graph[current_name].add(dep_name)
        
        # Ajouter les classes parentes comme dépendances
        for node in tree.body:
             if isinstance(node, ast.ClassDef):
                  current_name = node.name
                  for base in node.bases:
                       if isinstance(base, ast.Name):
                            parent_name = base.id
                            if parent_name in all_final_definitions:
                                 final_graph[current_name].add(parent_name)
                       # Ignorer les parents importés (ex: comfy.model_base.QwenImage)

        # import pprint; pprint.pprint(final_graph) # Optionnel: pour déboguer le graphe final
        print("--- Graphe de dépendances final construit. ---")
        return final_graph

    except Exception as e:
        print(f"--- ERREUR lors de la construction du graphe final: {e}. Élagage annulé. ---")
        traceback.print_exc()
        return None # Retourner None pour signaler l'échec

def remove_dead_code(definitions_code, final_dependency_graph, entry_points):
    print("--- Démarrage de l'élagage du code mort (v9 - Graphe Final) ---")
    try:
        tree = ast.parse(definitions_code)
        all_definitions = {node.name: node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))}
        print(f"  -> Trouvé {len(all_definitions)} définitions initiales.")

        reachable_names = set()
        # La file d'attente commence avec les points d'entrée valides qui existent réellement dans le code
        queue = [ep for ep in entry_points if ep in all_definitions]
        processed_in_queue = set(queue)

        print(f"  -> Points d'entrée initiaux valides: {queue}")
        if not queue:
             print("  -> AVERTISSEMENT: Aucun point d'entrée valide trouvé. L'élagage supprimera tout.")

        while queue:
            current_name = queue.pop(0)

            # On ne traite que les noms présents dans notre code final
            if current_name not in all_definitions or current_name in reachable_names:
                continue

            reachable_names.add(current_name)
            # print(f"  -> Atteint et marqué: {current_name}")

            # Explorer les dépendances via le GRAPHE FINAL (simple et direct)
            dependencies = final_dependency_graph.get(current_name, set())
            # print(f"    -> Dépendances pour '{current_name}': {dependencies}")

            for dep_name in dependencies:
                # Si la dépendance est dans notre code et pas encore traitée/marquée
                if dep_name in all_definitions and dep_name not in reachable_names and dep_name not in processed_in_queue:
                    # print(f"      -> Ajout à la file: '{dep_name}'")
                    queue.append(dep_name)
                    processed_in_queue.add(dep_name)

        # Filtrer et reconstruire (reste inchangé)
        kept_definitions_nodes = [node for name, node in all_definitions.items() if name in reachable_names]
        print(f"  -> Définitions atteignables trouvées ({len(reachable_names)}): {reachable_names}")

        if hasattr(ast, 'unparse'):
            original_order = {node.name: i for i, node in enumerate(tree.body) if isinstance(node, (ast.ClassDef, ast.FunctionDef))}
            kept_definitions_nodes.sort(key=lambda node: original_order.get(node.name, float('inf')))
            cleaned_code = "\n\n".join([ast.unparse(node) for node in kept_definitions_nodes])
            print(f"--- Élagage terminé. Conservé {len(kept_definitions_nodes)}/{len(all_definitions)} définitions. ---")
            return cleaned_code
        else:
            print("--- AVERTISSEMENT: ast.unparse non disponible (Python < 3.9). Élagage annulé. ---")
            return definitions_code

    except Exception as e:
        print(f"--- ERREUR pendant l'élagage: {e}. Utilisation du code non nettoyé. ---")
        traceback.print_exc()
        return definitions_code

async def generate_code_handler(request):
    build_indexes()
    
    try:
        data = await request.json()
        
        initial_classes_to_process = {node['class_name'] for node in data['executionOrder']}
        
        # Ligne corrigée : On récupère bien les deux valeurs retournées par le resolver
        resolver = DependencyResolver(NODE_CLASS_MAPPINGS)
        collected_imports, definitions_code = resolver.resolve(initial_classes_to_process)
  
        # ▼▼▼ PATCH POUR LA COLLISION 'Attention' ▼▼▼
        # On cherche le nom de la version Nunchaku de 'Attention'
        renamed_attention_class = None
        if resolver.rename_map:
            for key in resolver.rename_map.values():
                if 'Attention' in key and 'nunchaku' in key:
                    renamed_attention_class = key
                    break
    
        if renamed_attention_class:
            print("  -> Application du patch pour le conflit de nom 'Attention'.")
            # On s'assure que le code de Nunchaku appelle bien sa propre version de Attention
            definitions_code = definitions_code.replace("self.attn = Attention(", f"self.attn = {renamed_attention_class}(")
        # ▲▲▲ FIN DU PATCH 'Attention' ▲▲▲

        # ▼▼▼ APPLICATION DE LA RÈGLE SPÉCIALE "NUNCHAKU" (VERSION CORRIGÉE) ▼▼▼
        # On cherche le nom de la classe de config qui a été renommée
        renamed_config_class = None
        if resolver.rename_map: # S'assurer que la map n'est pas vide
            for key in resolver.rename_map.values():
                if 'NunchakuQwenImage' in key and 'configs' in key:
                    renamed_config_class = key
                    break

        if renamed_config_class:
            print("LOG: Application du patch final pour Nunchaku (avec gestion de l'indentation).")
        
            # 1. Le Pattern capture maintenant l'indentation du début de la ligne dans le groupe 1 (\s*)
            code_incorrect_pattern = re.compile(r'^(\s*)model_config\s*=\s*NunchakuQwenImage\s*\(\s*\{.*?\}\s*\)', re.DOTALL | re.MULTILINE)
        
            # 2. Le Remplacement est une simple ligne, SANS indentation
            code_correct = f"model_config = {renamed_config_class}({{'image_model': 'qwen_image', 'scale_shift': 0, 'rank': rank, 'precision': precision}})"
        
            # 3. On utilise une fonction de remplacement pour réappliquer l'indentation capturée
            def perform_replacement(match):
                indentation = match.group(1) # Récupère l'indentation originale (groupe 1)
                return f"{indentation}{code_correct}"
            definitions_code = definitions_code.replace("class NunchakuQwenImage(QwenImage):", 
                                                    "class NunchakuQwenImage(comfy.model_base.QwenImage):", 1)
            definitions_code = definitions_code.replace("super(QwenImage, self).__init__(",
                                                    "super(comfy.model_base.QwenImage, self).__init__(", 1)                                        
            definitions_code, count = re.subn(code_incorrect_pattern, perform_replacement, definitions_code, count=1)
            print("  -> Application du patch pour l'import relatif 'model_base'.")
            definitions_code = definitions_code.replace('model_base.NunchakuQwenImage', 'NunchakuQwenImage')
            if count > 0:
                print("  -> Patch d'indentation appliqué avec succès.")
            else:
                print("  -> AVERTISSEMENT: Le patch Nunchaku n'a pas trouvé le code à remplacer.")

        # ▲▲▲ FIN DU PATCH ▲▲▲

  
        sane_class_name = sanitize_title_for_variable(data['newClassName'])

        # ▼▼▼ APPEL DE LA FONCTION DE NETTOYAGE ▼▼▼
        # On nettoie le code APRES les patchs, mais AVANT l'assemblage final
        #definitions_code = remove_dead_code(definitions_code, resolver.dependency_graph, sane_class_name, resolver.rename_map)
        # ▲▲▲ FIN DE L'APPEL ▲▲▲

        NOODLE_TYPES = {'IMAGE', 'MODEL', 'LATENT', 'CLIP', 'VAE', 'CONDITIONING'}
        all_handled_inputs = set()
        if 'internalLinks' not in data: data['internalLinks'] = []
        if 'ioMap' not in data: data['ioMap'] = {'inputs': {}, 'outputs': {}}
        if 'inputs' not in data['ioMap']: data['ioMap']['inputs'] = {}
        for link in data['internalLinks']: all_handled_inputs.add(f"{link['target_id']}:{link['target_slot']}")
        for inp in data['ioMap']['inputs'].values(): all_handled_inputs.add(f"{inp['targetNodeId']}:{inp['targetNodeSlot']}")
        for node in data['executionOrder']:
            node_class = NODE_CLASS_MAPPINGS.get(node['class_name'])
            if not node_class: continue
            try:
                required_inputs = node_class.INPUT_TYPES().get('required', {})
                for i, input_slot_info in enumerate(node.get('inputs', [])):
                    input_name = input_slot_info.get('name')
                    input_type = input_slot_info.get('type')
                    if (input_name in required_inputs and
                        f"{node['id']}:{i}" not in all_handled_inputs and
                        input_type in NOODLE_TYPES):
                        new_input_name = f"{sanitize_title_for_variable(node.get('title', ''))}_{input_name}"
                        data['ioMap']['inputs'][new_input_name] = {'name': new_input_name, 'type': input_type, 'originalClassName': node.get('class_name'), 'originalInputName': input_name, 'targetNodeId': node.get('id'), 'targetNodeSlot': i}
            except Exception: pass

        base_imports = {"import logging", "logger = logging.getLogger(__name__)", "import torch", "import folder_paths", "from comfy import utils", "from comfy_api.latest import io", "import math", "import node_helpers"}
        final_imports_set = base_imports.union(collected_imports)

        # Le bloc de code bogué qui utilisait 'collected_code' a été supprimé.

        body_code_parts = []
        body_code_parts.append(f"class {sane_class_name}:")
        body_code_parts.append("    @classmethod")
        body_code_parts.append("    def INPUT_TYPES(s):")
        body_code_parts.append("        return { \"required\": {")
        io_inputs = data.get('ioMap', {}).get('inputs', {})
        for name, details in io_inputs.items():
          # ==================================================================
# == VERSION ULTIME DU BLOC try/except ==
# ==================================================================
          try:
              original_class_name = details.get('originalClassName', '')
              original_input_name = details.get('originalInputName', '')

              # ÉTAPE 1: Analyse AST pour type dynamique
              type_info_str = get_dynamic_input_str_from_source(original_class_name, original_input_name)

              # ÉTAPE 2: Récupération des infos complètes du nœud
              input_info = None
              node_class = NODE_CLASS_MAPPINGS.get(original_class_name)
              if node_class:
                  try:
                      input_defs = node_class.INPUT_TYPES()
                      input_info = input_defs.get('required', {}).get(original_input_name)
                  except:
                      pass

              # ÉTAPE 3: Fallback si l'analyse AST a échoué
              if type_info_str is None:
                  if input_info and isinstance(input_info[0], list):
                      type_info_str = repr(input_info[0])
                  else:
                      type_info_str = f'"{details.get("type", "*")}"'

              # ÉTAPE 4: Construction propre du tuple final
              tuple_parts = [type_info_str]
              if input_info and len(input_info) > 1:
                  tuple_parts.append(repr(input_info[1]))

              final_tuple_content = ", ".join(tuple_parts)
              if len(tuple_parts) == 1:
                  final_tuple_content += ","

              # ▼▼▼ LA CORRECTION FINALE EST ICI ▼▼▼
              # On remplace les appels spécifiques qui dépendent du contexte de leur classe d'origine.
              final_tuple_content = final_tuple_content.replace('s.vae_list()', "folder_paths.get_filename_list('vae')")

              body_code_parts.append(f"            \"{name}\": ({final_tuple_content}),")
          
          except Exception:
              body_code_parts.append(f"            \"{name}\": (\"*\",),")
        
        body_code_parts.append("        }}")

        outputs = data.get('ioMap', {}).get('outputs', {}).values()
        body_code_parts.append(f"    RETURN_TYPES = ({', '.join([f'\"{o.get("type", "UNKNOWN")}\"' for o in outputs])},)")
        body_code_parts.append(f"    RETURN_NAMES = ({', '.join([f'\"{n.get("name", "unknown")}\"' for n in outputs])},)")
        body_code_parts.append(f"    FUNCTION = \"execute\"")
        body_code_parts.append(f"    CATEGORY = \"{data.get('newCategory', 'Subgraph')}\"")
        
        input_keys = list(io_inputs.keys())
        body_code_parts.append(f"\n    def execute(self, {', '.join(input_keys)}):")
        
        output_vars = {}
        for node in data.get('executionOrder', []):
            instance_name = f"{sanitize_title_for_variable(node.get('title', ''))}_{node.get('id', '')}"
            node_class_name = node.get('class_name')
            if not node_class_name: continue
            
            node_class = NODE_CLASS_MAPPINGS.get(node_class_name)
            function_name = node_class.FUNCTION
            
            body_code_parts.append(f"\n        {instance_name} = {node_class_name}()")
            
            args = {}
            
            internal_links_for_node = [l for l in data.get('internalLinks', []) if l.get('target_id') == node.get('id')]
            for link in internal_links_for_node:
                if link.get('target_slot') is not None and link['target_slot'] < len(node.get('inputs', [])):
                    arg_name = node['inputs'][link['target_slot']]['name']
                    origin_node_id, origin_slot = link.get('origin_id'), link.get('origin_slot')
                    if origin_node_id in output_vars and origin_slot < len(output_vars[origin_node_id]):
                        args[arg_name] = output_vars[origin_node_id][origin_slot]
            
            exposed_inputs_for_node = [inp for inp in io_inputs.values() if inp.get('targetNodeId') == node.get('id')]
            for inp in exposed_inputs_for_node:
                if inp.get('targetNodeSlot') is not None and inp['targetNodeSlot'] < len(node.get('inputs', [])):
                    original_arg_name = node['inputs'][inp['targetNodeSlot']]['name']
                    args[original_arg_name] = inp['name']
            
            widget_values = node.get("widgets_values", [])
            if widget_values:
                try:
                    original_inputs = node_class.INPUT_TYPES().get("required", {})
                    widget_names = [name for name, props in original_inputs.items() if props[0] not in NOODLE_TYPES]
                    
                    value_idx = 0
                    for name in widget_names:
                         if name not in args and value_idx < len(widget_values):
                             args[name] = widget_values[value_idx]
                             value_idx += 1
                except:
                    pass
            
            args_parts = []
            for k, v in args.items():
                if isinstance(v, str) and (v in input_keys or v.startswith('out_')):
                    args_parts.append(f"{k}={v}")
                else:
                    args_parts.append(f"{k}={repr(v)}")
            args_str = ", ".join(args_parts)

            return_vars = [f"out_{node.get('id', '')}_{i}" for i in range(len(node.get('outputs',[])))]
            output_vars[node.get('id', '')] = return_vars

            if return_vars:
                body_code_parts.append(f"        ({', '.join(return_vars)},) = {instance_name}.{function_name}({args_str})")
            else:
                body_code_parts.append(f"        {instance_name}.{function_name}({args_str})")

        final_return_vars = [output_vars[out['originNodeId']][out['originNodeSlot']] for out in outputs if out.get('originNodeId') in output_vars]
        body_code_parts.append(f"\n        return ({', '.join(final_return_vars)},)")
        
        naive_code_body = "\n".join(body_code_parts)
        
        
        # ▼▼▼ AJOUT MINIMAL POUR L'ÉLAGAGE ▼▼▼
        # 1. Trouver les points d'entrée en appelant la nouvelle fonction
        entry_points = _find_entry_points_from_execute(naive_code_body, resolver)

        # 2. Construire le graphe de dépendances FINAL à partir du code patché
        final_graph = build_final_dependency_graph(definitions_code)
        
        # 3. Appeler la fonction de nettoyage simplifiée avec le nouveau graphe
        if entry_points and final_graph is not None:
             # Important: On passe bien final_graph ici !
            definitions_code = remove_dead_code(definitions_code, final_graph, entry_points)
        else:
            print("--- AVERTISSEMENT: Points d'entrée non trouvés ou erreur graphe final. Élagage annulé. ---")
        # ▲▲▲ FIN DE LA NOUVELLE LOGIQUE ▲▲▲
        
        final_future = sorted([imp for imp in final_imports_set if '__future__' in imp])
        final_other = sorted([imp for imp in final_imports_set if '__future__' not in imp])
        final_import_code = "\n".join(final_future + final_other)

        final_code_output = (
            f"# ---Don't use this file for build_indexes--- \n\n"
            f"# Fichier généré par le Subgraph Compiler (vFinal)\n\n"
            f"{final_import_code}\n\n"
            f"# --- Définitions des classes et fonctions nécessaires ---\n"
            f"{definitions_code}\n\n"
            f"# --- Nœud principal du sous-graphe ---\n"
            f"{naive_code_body}\n\n"
            f"# --- Mappings pour ComfyUI ---\n"
            f"NODE_CLASS_MAPPINGS = {{ \"{sane_class_name}\": {sane_class_name} }}\n"
            f"NODE_DISPLAY_NAME_MAPPINGS = {{ \"{data.get('newClassName', sane_class_name)}\": \"{sane_class_name}\" }}\n"
        )
        return web.Response(text=final_code_output, content_type='text/plain')

    except Exception as e:
        return web.Response(status=500, text=f"Erreur lors de la génération du code: {e}\n{traceback.format_exc()}")

# ===============================================================
# --- ENREGISTREMENT DES ROUTES API ---
# ===============================================================
def add_api_routes(app):
    print("✅ Ajout des routes API pour le Subgraph Compiler...")
    app.add_routes([
        web.get('/subgraph_compiler/get_node_source', get_node_source),
        web.post('/subgraph_compiler/generate_code', generate_code_handler)
    ])