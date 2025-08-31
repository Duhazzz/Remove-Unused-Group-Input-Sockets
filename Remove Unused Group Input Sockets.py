bl_info = {
    "name": "Remove Unused Group Input Sockets",
    "author": "ChatGPT, duhazzz",
    "version": (1, 0, 0),
    "blender": (3, 0, 0),
    "location": "Node Editor > N-Panel > Utilities",
    "description": "Удаляет неиспользуемые входные сокеты Group Input в нод-группах",
    "category": "Node",
}

import bpy

def _iter_interface_inputs(nt):
    """Возвращает итератор по интерфейсным ВХОДНЫМ сокетам (и их контейнеру) для разных версий API."""
    # Blender 3.x: nt.inputs — коллекция интерфейсных входов
    if hasattr(nt, "inputs"):
        coll = getattr(nt, "inputs", None)
        if coll is not None:
            for s in list(coll):
                yield ("inputs", coll, s)

    # Blender 4.x: nt.interface.items_tree — смешанная коллекция (панели + сокеты)
    iface = getattr(nt, "interface", None)
    items = getattr(iface, "items_tree", None)
    if iface is not None and items is not None:
        for item in list(items):
            # берём только сокеты и только INPUT
            if hasattr(item, "in_out") and getattr(item, "in_out", "") == 'INPUT':
                yield ("interface", items, item)

def _remove_interface_socket(nt, item):
    """Безопасно удаляет интерфейсный сокет, независимо от версии API."""
    # Blender 3.x
    if hasattr(nt, "inputs") and item in getattr(nt, "inputs", []):
        nt.inputs.remove(item)
        return True
    # Blender 4.x
    iface = getattr(nt, "interface", None)
    if iface and hasattr(iface, "remove"):
        try:
            iface.remove(item)
            return True
        except Exception:
            pass
    # Fallback на поиск по идентификатору
    ident = getattr(item, "identifier", None)
    if ident:
        for _, _, s in _iter_interface_inputs(nt):
            if getattr(s, "identifier", None) == ident:
                try:
                    _remove_interface_socket(nt, s)
                    return True
                except Exception:
                    pass
    return False

def _find_interface_input_by_identifier(nt, identifier):
    for _, _, s in _iter_interface_inputs(nt):
        if getattr(s, "identifier", None) == identifier:
            return s
    return None

def remove_unused_group_inputs_in_tree(nt, *, report_list=None):
    """Удаляет неиспользуемые выходы у узла Group Input и соответствующие интерфейсные входы."""
    if not getattr(nt, "nodes", None):
        return 0

    removed = 0
    # ищем все узлы Group Input внутри группы
    gi_nodes = [n for n in nt.nodes if getattr(n, "type", "") == 'GROUP_INPUT' or n.bl_idname == "NodeGroupInput"]
    if not gi_nodes:
        return 0

    # Каждый выходной сокет Group Input соответствует интерфейсному ВХОДУ группы.
    # Проверяем привязки по socket.identifier — это надёжно между версиями.
    # Собираем множество идентификаторов неиспользуемых сокетов (если такие есть на КАЖДОМ Group Input).
    # (обычно узел один, но если несколько — считаем сокет неиспользуемым, только если ни на одном узле он не связан)
    id_to_linked = {}

    # Сначала проставим linked-флаг по всем GI-нодам
    for gi in gi_nodes:
        for out in gi.outputs:
            ident = getattr(out, "identifier", None)
            if ident is None:
                # запасной путь: индекс как идентификатор
                ident = f"__index__{gi.outputs.find(out.name)}"
            was = id_to_linked.get(ident, False)
            id_to_linked[ident] = was or out.is_linked

    # Теперь удалим те интерфейсные входы, чьи идентификаторы нигде не связаны
    # Идём в обратном порядке интерфейса, чтобы сохранять стабильность индексов в старом API
    iface_inputs = list(_iter_interface_inputs(nt))
    for _, _, iface_socket in reversed(iface_inputs):
        ident = getattr(iface_socket, "identifier", None)
        if ident is None:
            # в старом API может не быть identifier — попробуем сопоставить по индексу/имени
            # метод: находим соответствующий выход на любом GI-ноде с тем же индексом
            # если НИ один такой выход не связан — удаляем
            # (менее надёжно, но спасёт редкие случаи)
            idx_guess = None
            # попробуем добыть индекс через коллекцию nt.inputs
            if hasattr(nt, "inputs"):
                try:
                    idx_guess = list(nt.inputs).index(iface_socket)
                except Exception:
                    idx_guess = None
            is_linked = False
            if idx_guess is not None:
                for gi in gi_nodes:
                    if idx_guess < len(gi.outputs):
                        if gi.outputs[idx_guess].is_linked:
                            is_linked = True
                            break
            if not is_linked:
                if _remove_interface_socket(nt, iface_socket):
                    removed += 1
                    if report_list is not None:
                        report_list.append((nt.name, getattr(iface_socket, "name", "Unnamed")))
            continue

        # обычный путь по identifier
        if not id_to_linked.get(ident, False):
            if _remove_interface_socket(nt, iface_socket):
                removed += 1
                if report_list is not None:
                    report_list.append((nt.name, getattr(iface_socket, "name", "Unnamed")))

    return removed


class NODE_OT_remove_unused_group_inputs(bpy.types.Operator):
    bl_idname = "node.remove_unused_group_inputs"
    bl_label = "Remove Unused Group Inputs"
    bl_options = {'REGISTER', 'UNDO'}

    scope: bpy.props.EnumProperty(
        name="Scope",
        items=[
            ('ACTIVE', "Active Node Tree", "Обработать только текущую нод-группу/дерево"),
            ('ALL', "All Node Groups", "Обработать все нод-группы в файле"),
        ],
        default='ACTIVE',
    )

    def execute(self, context):
        report = []
        total = 0

        def process(nt):
            nonlocal total
            total += remove_unused_group_inputs_in_tree(nt, report_list=report)

        if self.scope == 'ACTIVE':
            nt = getattr(context.space_data, "edit_tree", None) or getattr(context.space_data, "node_tree", None)
            if nt is None:
                self.report({'WARNING'}, "Нет активного Node Tree")
                return {'CANCELLED'}
            process(nt)
        else:
            for nt in bpy.data.node_groups:
                process(nt)

        if total == 0:
            self.report({'INFO'}, "Неиспользуемые входы не найдены")
        else:
            self.report({'INFO'}, f"Удалено входов: {total}")

        return {'FINISHED'}


class NODE_PT_remove_unused_group_inputs(bpy.types.Panel):
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Utilities"
    bl_label = "Group Inputs Cleanup"

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.operator(NODE_OT_remove_unused_group_inputs.bl_idname, text="Clean Active").scope = 'ACTIVE'
        col.operator(NODE_OT_remove_unused_group_inputs.bl_idname, text="Clean All").scope = 'ALL'


classes = (
    NODE_OT_remove_unused_group_inputs,
    NODE_PT_remove_unused_group_inputs,
)

def register():
    for c in classes:
        bpy.utils.register_class(c)

def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)

if __name__ == "__main__":
    register()
