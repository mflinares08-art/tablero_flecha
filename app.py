import zoneinfo
from datetime import datetime
import re
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import streamlit as st

# Zona Horaria de Argentina
TZ_ARG = zoneinfo.ZoneInfo("America/Argentina/Buenos_Aires")

# ---------------------------------------------------------
# CONFIGURACIÓN DE PÁGINA
# ---------------------------------------------------------
st.set_page_config(
    page_title="Tablero en Vivo - Grupo Flecha",
    page_icon="🚌",
    layout="wide",
    initial_sidebar_state="expanded",
)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

ID_SHEET = "1b-FnwWgy9bvdM83FQ2E_A7xp8bl9-OLkMT8VZVSIuuo"

cols_orden = [
    "FECHA",
    "CODIGO",
    "CABECERA",
    "HORARIO",
    "ANUNCIO",
    "EMPRESA",
    "INTERNO",
    "PLAT",
    "PARTIO",
    "DEMORA",
    "ESTADO",
    "COMENTARIOS",
    "GUARDADO",
]


# ---------------------------------------------------------
# CONEXIÓN Y FUNCIONES DE GOOGLE SHEETS
# ---------------------------------------------------------
def obtener_cliente_gspread():
    if "gcp_service_account" not in st.secrets:
        raise ValueError("No se encontró 'gcp_service_account' en st.secrets")

    creds_dict = dict(st.secrets["gcp_service_account"])
    if "private_key" in creds_dict:
        creds_dict["private_key"] = creds_dict["private_key"].replace(
            "\\n", "\n"
        )

    credentials = Credentials.from_service_account_info(
        creds_dict, scopes=SCOPES
    )
    return gspread.authorize(credentials)


@st.cache_data(ttl=15)
def cargar_pestana_flexible(nombre_buscado):
    try:
        gc = obtener_cliente_gspread()
        spreadsheet = gc.open_by_key(ID_SHEET)

        target_sheet = None
        for w in spreadsheet.worksheets():
            nombre_limpio = (
                w.title.lower()
                .replace("ó", "o")
                .replace("í", "i")
                .replace("á", "a")
                .strip()
            )
            buscado_limpio = (
                nombre_buscado.lower()
                .replace("ó", "o")
                .replace("í", "i")
                .replace("á", "a")
                .strip()
            )
            if nombre_limpio == buscado_limpio:
                target_sheet = w
                break

        if not target_sheet:
            return pd.DataFrame()

        data = target_sheet.get_all_values()
        if not data or len(data) < 1:
            return pd.DataFrame()

        headers = [str(h).strip() for h in data[0]]

        if len(data) == 1:
            return pd.DataFrame(columns=headers)

        df = pd.DataFrame(data[1:], columns=headers)
        return df
    except Exception as e:
        st.warning(f"Aviso al cargar '{nombre_buscado}': {e}")
        return pd.DataFrame()


def guardar_todo_en_sheets(df_completo):
    gc = obtener_cliente_gspread()
    spreadsheet = gc.open_by_key(ID_SHEET)

    sh = None
    for w in spreadsheet.worksheets():
        if "plantilla" in w.title.lower() or "partida" in w.title.lower():
            sh = w
            break

    if not sh:
        sh = spreadsheet.worksheet("plantilla_partidas")

    df_a_enviar = df_completo.copy().fillna("")
    df_a_enviar["CABECERA"] = (
        df_a_enviar["CABECERA"].astype(str).str.replace("🟡", "").str.strip()
    )

    if "MENSAJE WA" in df_a_enviar.columns:
        df_a_enviar = df_a_enviar.drop(columns=["MENSAJE WA"])

    matriz_datos = [
        df_a_enviar.columns.tolist()
    ] + df_a_enviar.astype(str).values.tolist()

    sh.clear()
    sh.update(range_name="A1", values=matriz_datos)
    st.cache_data.clear()


def parsear_hora(texto_hora):
    if not texto_hora or pd.isna(texto_hora):
        return None
    match = re.search(r"(\d{1,2}):(\d{2})", str(texto_hora).strip())
    if match:
        h, m = map(int, match.groups())
        return h * 60 + m
    return None


def ordenar_por_horario(df_input):
    if df_input.empty or "HORARIO" not in df_input.columns:
        return df_input

    df_aux = df_input.copy()
    df_aux["_minutos_sort"] = df_aux["HORARIO"].apply(
        lambda x: parsear_hora(x) if parsear_hora(x) is not None else 9999
    )
    df_aux = df_aux.sort_values(by=["_minutos_sort"]).drop(
        columns=["_minutos_sort"]
    )
    return df_aux.reset_index(drop=True)


def calcular_demora_y_estado(row):
    min_horario = parsear_hora(row.get("HORARIO", ""))
    min_partio = parsear_hora(row.get("PARTIO", ""))

    if min_horario is None or min_partio is None:
        return pd.Series([0, "⏳ Pendiente"])

    diff_min = min_partio - min_horario

    if diff_min < -720:
        diff_min += 1440

    if diff_min < 0:
        diff_min = 0

    estado = "🟢 En Horario" if diff_min < 10 else "🔴 Demorado"
    return pd.Series([diff_min, estado])


def armar_mensaje_despacho(row):
    horario = str(row.get("HORARIO", "")).strip()
    empresa = str(row.get("EMPRESA", "")).strip()
    anuncio = str(row.get("ANUNCIO", "")).strip()
    estado = str(row.get("ESTADO", "")).strip()
    demora = row.get("DEMORA", 0)
    comentarios = str(row.get("COMENTARIOS", "")).strip()

    if "Demorado" in estado and demora > 0:
        texto_estado = f"sale {demora} min demorado de Retiro"
    else:
        texto_estado = "sale a Horario de Retiro"

    frase = f"{horario} {empresa} a {anuncio} {texto_estado}"

    if comentarios and comentarios.upper() != "NAN" and comentarios != "":
        frase += f" - {comentarios}"

    return frase


# ---------------------------------------------------------
# CARGA DE DATOS DESDE SHEETS
# ---------------------------------------------------------
try:
    df_diario = cargar_pestana_flexible("plantilla_partidas")
    df_base = cargar_pestana_flexible("Base_Servicios")
    df_codigos_sheet = cargar_pestana_flexible("codigos")
except Exception as e:
    st.error(f"❌ Error al conectar con Google Sheets: {e}")
    st.stop()

# Normalización de df_diario
if not df_diario.empty:
    for col in cols_orden:
        if col not in df_diario.columns:
            df_diario[col] = ""
    df_diario = df_diario[cols_orden].copy()
else:
    df_diario = pd.DataFrame(columns=cols_orden)

df_trabajo = ordenar_por_horario(df_diario.copy())

if not df_trabajo.empty:
    df_trabajo[["DEMORA", "ESTADO"]] = df_trabajo.apply(
        calcular_demora_y_estado, axis=1
    )

st.session_state["df_trabajo"] = df_trabajo

# Mapeo de Base_Servicios asignando la Columna R ('Código de transportista')
dict_base = {}
if not df_base.empty:
    col_map = {c.lower().strip(): c for c in df_base.columns}

    col_cod = col_map.get("codigo", df_base.columns[0])
    col_salida = col_map.get("fecha salida", None)
    col_origen = col_map.get("origen", None)
    col_anuncio = col_map.get("se anuncia a", None)
    col_interno = col_map.get("interno", None)

    # Buscar 'Código de transportista' (Columna R - posición 17)
    col_empresa = col_map.get("codigo de transportista", None)
    if not col_empresa and len(df_base.columns) > 17:
        col_empresa = df_base.columns[17]  # Columna R

    for _, row in df_base.iterrows():
        cod_key = str(row[col_cod]).strip().upper()
        if cod_key and cod_key != "NAN":
            raw_salida = str(row[col_salida]) if col_salida else ""
            match_h = re.search(r"(\d{1,2}):(\d{2})", raw_salida)
            if match_h:
                h, m = match_h.groups()
                horario_fmt = f"{int(h):02d}:{m}"
            else:
                horario_fmt = ""

            dict_base[cod_key] = {
                "CABECERA": str(row[col_origen]).strip() if col_origen else "",
                "HORARIO": horario_fmt,
                "ANUNCIO": str(row[col_anuncio]).strip() if col_anuncio else "",
                "EMPRESA": str(row[col_empresa]).strip() if col_empresa else "",
                "INTERNO": str(row[col_interno]).strip() if col_interno else "",
            }

# ---------------------------------------------------------
# INTERFAZ & ESTILOS
# ---------------------------------------------------------
st.markdown(
    """
<style>
    .stApp { background-color: #0d1b2a; color: #e0e1dd; }
    div[data-testid="stMetric"] {
        background-color: #1b263b;
        border: 1px solid #415a77;
        padding: 15px;
        border-radius: 10px;
    }
    section[data-testid="stSidebar"] { background-color: #1b263b; }
    h1, h2, h3 { color: #ffffff !important; }
</style>
""",
    unsafe_allow_html=True,
)

st.title("🌐 Tablero en Vivo - Grupo Flecha")
st.markdown("---")

# ---------------------------------------------------------
# FILTROS LATERALES Y OPCIONES
# ---------------------------------------------------------
st.sidebar.header("🔍 Filtros & Opciones")

if st.sidebar.button("🔄 Refrescar Nube", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

auto_refresco = st.sidebar.checkbox("📡 Auto-actualizar cada 30 seg", value=False)
if auto_refresco:
    import time
    time.sleep(30)
    st.cache_data.clear()
    st.rerun()

st.sidebar.markdown("---")

fecha_hoy = datetime.now(TZ_ARG).date()
if "fecha_seleccionada" not in st.session_state:
    st.session_state["fecha_seleccionada"] = fecha_hoy

fecha_sel = st.sidebar.date_input(
    "📅 Seleccionar Fecha",
    value=st.session_state["fecha_seleccionada"],
    key="fecha_seleccionada",
)
fecha_sel_str = str(fecha_sel)

empresas = ["Todas las empresas"] + sorted(
    list(
        set(
            str(e).strip()
            for e in df_trabajo["EMPRESA"].dropna().unique()
            if str(e).strip() and str(e).strip().upper() != "NAN"
        )
    )
)
empresa_sel = st.sidebar.selectbox("Empresa", empresas)

estados = (
    list(df_trabajo["ESTADO"].unique())
    if not df_trabajo.empty
    else []
)
estados_sel = st.sidebar.multiselect("Estado", estados, default=estados)

buscar_destino = st.sidebar.text_input(
    "Anuncio / Destino", placeholder="Buscar ciudad..."
)

# ---------------------------------------------------------
# FORMULARIO MANUAL
# ---------------------------------------------------------
st.sidebar.markdown("---")
st.sidebar.subheader("➕ Agregar Servicio Manual")

if "form_manual" not in st.session_state:
    st.session_state["form_manual"] = {
        "CODIGO": "",
        "CABECERA": "",
        "HORARIO": "",
        "ANUNCIO": "",
        "EMPRESA": "",
        "INTERNO": "",
    }

cod_ingresado = st.sidebar.text_input(
    "Código de Servicio",
    value=st.session_state["form_manual"]["CODIGO"],
    key="input_cod_manual",
)

col_auto1, col_auto2 = st.sidebar.columns([1, 1])
if col_auto1.button("🔍 Buscar Datos"):
    if not cod_ingresado.strip():
        st.sidebar.warning("Ingresá un código para buscar.")
    else:
        key_search = cod_ingresado.strip().upper()
        if key_search in dict_base:
            info_b = dict_base[key_search]
            st.session_state["form_manual"] = {
                "CODIGO": cod_ingresado.strip(),
                "CABECERA": info_b["CABECERA"],
                "HORARIO": info_b["HORARIO"],
                "ANUNCIO": info_b["ANUNCIO"],
                "EMPRESA": info_b["EMPRESA"],
                "INTERNO": info_b["INTERNO"],
            }
            st.sidebar.success("¡Datos encontrados!")
            st.rerun()
        else:
            st.sidebar.error("Código no encontrado en Base_Servicios.")

with st.sidebar.form("form_nuevo_servicio"):
    f_cabecera = st.text_input(
        "Cabecera", value=st.session_state["form_manual"]["CABECERA"]
    )
    f_horario = st.text_input(
        "Horario Salida (HH:MM)*",
        value=st.session_state["form_manual"]["HORARIO"],
        placeholder="14:30",
    )
    f_anuncio = st.text_input(
        "Anuncio / Destino",
        value=st.session_state["form_manual"]["ANUNCIO"],
    )
    f_empresa = st.text_input(
        "Empresa", value=st.session_state["form_manual"]["EMPRESA"]
    )
    f_interno = st.text_input(
        "Interno", value=st.session_state["form_manual"]["INTERNO"]
    )

    btn_agregar = st.form_submit_button("➕ Añadir y Guardar en Nube")

    if btn_agregar:
        if not cod_ingresado or not f_horario:
            st.error("⚠️ Código y Horario son requeridos.")
        else:
            nueva_fila = {
                "FECHA": fecha_sel_str,
                "CODIGO": str(cod_ingresado).strip(),
                "CABECERA": str(f_cabecera).strip(),
                "HORARIO": str(f_horario).strip(),
                "ANUNCIO": str(f_anuncio).strip(),
                "EMPRESA": str(f_empresa).strip(),
                "INTERNO": str(f_interno).strip(),
                "PLAT": "",
                "PARTIO": "",
                "DEMORA": 0,
                "ESTADO": "⏳ Pendiente",
                "COMENTARIOS": "",
                "GUARDADO": "NO",
            }

            df_actualizado = pd.concat(
                [df_trabajo, pd.DataFrame([nueva_fila])],
                ignore_index=True,
            )
            df_actualizado = ordenar_por_horario(df_actualizado)
            guardar_todo_en_sheets(df_actualizado)

            st.session_state["form_manual"] = {
                "CODIGO": "",
                "CABECERA": "",
                "HORARIO": "",
                "ANUNCIO": "",
                "EMPRESA": "",
                "INTERNO": "",
            }
            st.success(f"✅ ¡Servicio {cod_ingresado} guardado en la nube!")
            st.rerun()

# ---------------------------------------------------------
# FILTRADO Y PROGRAMACIÓN DE DÍA
# ---------------------------------------------------------
mask = (
    df_trabajo["FECHA"].astype(str) == fecha_sel_str
    if not df_trabajo.empty
    else pd.Series(dtype=bool)
)

if empresa_sel != "Todas las empresas" and not df_trabajo.empty:
    mask = mask & (df_trabajo["EMPRESA"] == empresa_sel)

if estados_sel and not df_trabajo.empty:
    mask = mask & (df_trabajo["ESTADO"].isin(estados_sel))

if buscar_destino and not df_trabajo.empty:
    mask = mask & (
        df_trabajo["ANUNCIO"].str.contains(buscar_destino, case=False, na=False)
    )

df_filtrado = (
    df_trabajo[mask].copy()
    if not df_trabajo.empty
    else pd.DataFrame(columns=cols_orden)
)

# Carga Automática inicial
if df_filtrado.empty:
    st.info(
        f"📅 No hay servicios inicializados para la fecha **{fecha_sel_str}**."
    )
    col_cargar, _ = st.columns([2, 1])
    with col_cargar:
        if st.button(
            f"📥 Programar día {fecha_sel_str} desde pestaña 'codigos'",
            type="primary",
            use_container_width=True,
        ):
            if df_codigos_sheet.empty:
                st.error("❌ No se encontraron datos en la pestaña 'codigos'.")
            else:
                col_c = df_codigos_sheet.columns[0]
                codigos_lista = (
                    df_codigos_sheet[col_c]
                    .astype(str)
                    .str.strip()
                    .dropna()
                    .unique()
                    .tolist()
                )
                codigos_lista = [
                    c for c in codigos_lista if c != "" and c.upper() != "NONE" and c.upper() != "NAN"
                ]

                if not codigos_lista:
                    st.warning("⚠️ No se encontraron códigos válidos en la pestaña 'codigos'.")
                else:
                    nuevas_filas = []

                    for c_code in codigos_lista:
                        c_key = str(c_code).strip().upper()
                        info_base = dict_base.get(c_key, {})

                        f_item = {
                            "FECHA": fecha_sel_str,
                            "CODIGO": c_code,
                            "CABECERA": info_base.get("CABECERA", ""),
                            "HORARIO": info_base.get("HORARIO", ""),
                            "ANUNCIO": info_base.get("ANUNCIO", ""),
                            "EMPRESA": info_base.get("EMPRESA", ""),
                            "INTERNO": info_base.get("INTERNO", ""),
                            "PLAT": "",
                            "PARTIO": "",
                            "DEMORA": 0,
                            "ESTADO": "⏳ Pendiente",
                            "COMENTARIOS": "",
                            "GUARDADO": "NO",
                        }
                        nuevas_filas.append(f_item)

                    df_nueva_prog = pd.DataFrame(nuevas_filas)

                    df_mezclado = pd.concat([df_trabajo, df_nueva_prog], ignore_index=True)
                    df_mezclado = ordenar_por_horario(df_mezclado)

                    guardar_todo_en_sheets(df_mezclado)
                    st.success(f"🎉 ¡Se programaron {len(df_nueva_prog)} servicios para el día {fecha_sel_str}!")
                    st.rerun()

# ---------------------------------------------------------
# MÉTRICAS
# ---------------------------------------------------------
total_reg = len(df_filtrado)
en_tiempo = len(df_filtrado[df_filtrado["ESTADO"] == "🟢 En Horario"])
con_demora = len(df_filtrado[df_filtrado["ESTADO"] == "🔴 Demorado"])

porc_puntualidad = int((en_tiempo / total_reg * 100)) if total_reg > 0 else 0
porc_demora = int((con_demora / total_reg * 100)) if total_reg > 0 else 0

demoras_num = df_filtrado[df_filtrado["ESTADO"] == "🔴 Demorado"][
    "DEMORA"
].tolist()
prom_demora_val = (
    round(sum(demoras_num) / len(demoras_num), 1) if demoras_num else 0
)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Registros", f"{total_reg}", f"Fecha: {fecha_sel_str}")
col2.metric("🟢 En Horario", f"{en_tiempo}", f"{porc_puntualidad}% puntualidad")
col3.metric(
    "🔴 Demorados",
    f"{con_demora}",
    f"{porc_demora}% demorados",
    delta_color="inverse",
)
col4.metric(
    "Prom. Demora",
    f"{prom_demora_val} min" if prom_demora_val > 0 else "0 min",
    "en viajes demorados",
)

st.markdown("<br/>", unsafe_allow_html=True)

# ---------------------------------------------------------
# CALLBACK: IMPACTA DIRECTO EN LA NUBE TRAS EDITAR
# ---------------------------------------------------------
def callback_guardar_ediciones():
    edits = st.session_state.get("editor_tabla", {}).get("edited_rows", {})
    if edits and "df_indices_filtrados" in st.session_state:
        indices_filtrados = st.session_state["df_indices_filtrados"]
        hubo_cambio = False

        df_temp = st.session_state["df_trabajo"].copy()

        for pos_str, cambios in edits.items():
            pos_int = int(pos_str)
            if pos_int < len(indices_filtrados):
                idx_real = indices_filtrados[pos_int]
                for campo, val in cambios.items():
                    if campo != "MENSAJE WA":
                        val_limpio = str(val).replace("🟡", "").strip()
                        df_temp.at[idx_real, campo] = val_limpio
                        hubo_cambio = True

        if hubo_cambio:
            df_temp[["DEMORA", "ESTADO"]] = df_temp.apply(
                calcular_demora_y_estado, axis=1
            )
            df_temp = ordenar_por_horario(df_temp)
            guardar_todo_en_sheets(df_temp)


# ---------------------------------------------------------
# TABLA INTERACTIVA DE DATOS
# ---------------------------------------------------------
col_sub, col_btn = st.columns([3, 1])
with col_sub:
    st.subheader(f"📡 Despachos del día: {fecha_sel_str}")

with col_btn:
    csv = df_filtrado.to_csv(index=False).encode("utf-8")
    st.download_button(
        "📥 Exportar CSV",
        data=csv,
        file_name=f"tablero_flecha_{fecha_sel_str}.csv",
        mime="text/csv",
    )


def aplicar_colores(row):
    estado = str(row.get("ESTADO", ""))
    cabecera = str(row.get("CABECERA", "")).replace("🟡", "").strip().upper()

    if estado == "🔴 Demorado":
        return ["background-color: rgba(239, 68, 68, 0.25); color: #ff9999;"] * len(row)
    elif cabecera != "RET" and cabecera != "":
        return ["background-color: rgba(245, 158, 11, 0.22); color: #fde047;"] * len(row)
    elif estado == "🟢 En Horario":
        return ["background-color: rgba(34, 197, 94, 0.2); color: #99ffbb;"] * len(row)

    return [""] * len(row)


if not df_filtrado.empty:
    st.session_state["df_indices_filtrados"] = df_filtrado.index.tolist()

    cols_disabled = ["DEMORA", "ESTADO"]

    df_vista = df_filtrado.copy()

    def agregar_emoji_cabecera(val):
        str_val = str(val).replace("🟡", "").strip()
        if str_val.upper() != "RET" and str_val != "":
            return f"🟡 {str_val}"
        return str_val

    df_vista["CABECERA"] = df_vista["CABECERA"].apply(agregar_emoji_cabecera)
    df_vista["MENSAJE WA"] = df_filtrado.apply(armar_mensaje_despacho, axis=1)

    es_guardado_dia = (
        df_filtrado["GUARDADO"].astype(str).str.upper().eq("SI").all()
    )
    if es_guardado_dia:
        cols_disabled.extend(
            [
                "FECHA",
                "CODIGO",
                "CABECERA",
                "HORARIO",
                "ANUNCIO",
                "EMPRESA",
                "GUARDADO",
            ]
        )

    df_estilizado = df_vista.style.apply(aplicar_colores, axis=1)

    st.data_editor(
        df_estilizado,
        use_container_width=True,
        height=420,
        num_rows="dynamic",
        disabled=cols_disabled,
        column_config={
            "FECHA": st.column_config.TextColumn("FECHA"),
            "CABECERA": st.column_config.TextColumn("CABECERA"),
            "HORARIO": st.column_config.TextColumn("HORARIO (RET)"),
            "PARTIO": st.column_config.TextColumn("PARTIO (HH:MM)"),
            "DEMORA": st.column_config.NumberColumn(
                "DEMORA (min)", format="%d min"
            ),
            "ESTADO": st.column_config.TextColumn("ESTADO"),
            "COMENTARIOS": st.column_config.TextColumn("COMENTARIOS"),
            "MENSAJE WA": st.column_config.TextColumn("📋 MENSAJE WHATSAPP", help="Copiar directamente este texto"),
            "GUARDADO": st.column_config.TextColumn("GUARDADO"),
        },
        key="editor_tabla",
        on_change=callback_guardar_ediciones,
    )

# ---------------------------------------------------------
# BOTÓN DE CIERRE DE DÍA
# ---------------------------------------------------------
st.markdown("---")
if st.button(
    "💾 Confirmar y Cerrar Día en Google Sheets",
    type="primary",
    use_container_width=True,
):
    try:
        mask_guardar = df_trabajo["FECHA"].astype(str) == fecha_sel_str
        df_trabajo.loc[mask_guardar, "GUARDADO"] = "SI"

        guardar_todo_en_sheets(df_trabajo)

        st.success(f"✅ Día {fecha_sel_str} marcado como GUARDADO correctamente.")
        st.rerun()
    except Exception as err:
        st.error(f"❌ Error al guardar en Google Sheets: {err}")