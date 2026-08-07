import zoneinfo
from datetime import datetime, timedelta
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


# ---------------------------------------------------------
# FUNCIONES DE CONEXIÓN Y PROCESAMIENTO
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
def cargar_pestana(nombre_o_index="plantilla_partidas"):
    gc = obtener_cliente_gspread()
    spreadsheet = gc.open_by_key(ID_SHEET)

    if isinstance(nombre_o_index, int):
        sheet = spreadsheet.get_worksheet(nombre_o_index)
    else:
        sheet = spreadsheet.worksheet(nombre_o_index)

    data = sheet.get_all_values()
    if not data or len(data) < 2:
        return pd.DataFrame()

    headers = [str(h).strip() for h in data[0]]
    return pd.DataFrame(data[1:], columns=headers)


def parsear_hora(texto_hora):
    if not texto_hora or pd.isna(texto_hora):
        return None
    match = re.search(r"(\d{1,2}):(\d{2})", str(texto_hora).strip())
    if match:
        h, m = map(int, match.groups())
        return h * 60 + m
    return None


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


# ---------------------------------------------------------
# CARGA Y VÍNCULO DE BASE DE DATOS
# ---------------------------------------------------------
try:
    df_diario = cargar_pestana("plantilla_partidas")  # Pestaña de Partidas
    df_base = cargar_pestana("Base_Servicios")  # Base maestra de servicios
except Exception as e:
    st.error(f"❌ Error al conectar con Google Sheets: {e}")
    st.stop()

if df_diario.empty:
    st.warning("⚠️ La pestaña plantilla_partidas está vacía.")
    st.stop()

# 1. Limpiar encabezados y descartar filas vacías en CODIGO
df_diario.columns = df_diario.columns.str.upper()
df_diario = df_diario[
    df_diario["CODIGO"].astype(str).str.strip() != ""
].copy()

# Si la columna FECHA no existe en la hoja, asignarle la fecha actual de Argentina
fecha_hoy_str = datetime.now(TZ_ARG).strftime("%Y-%m-%d")
if "FECHA" not in df_diario.columns:
    df_diario["FECHA"] = fecha_hoy_str
else:
    df_diario["FECHA"] = (
        df_diario["FECHA"].replace("", fecha_hoy_str).fillna(fecha_hoy_str)
    )

# 2. Procesar Base Maestra si existe
if not df_base.empty:
    renombres = {
        "Código": "CODIGO",
        "Origen": "CABECERA_BASE",
        "Se anuncia a": "ANUNCIO_BASE",
        "Código de transportista": "EMPRESA_BASE",
        "Interno": "INTERNO_BASE",
    }
    df_base_clean = df_base.rename(columns=renombres).copy()

    if "Fecha salida" in df_base_clean.columns:
        df_base_clean["HORARIO_BASE"] = (
            pd.to_datetime(df_base_clean["Fecha salida"], errors="coerce")
            .dt.strftime("%H:%M")
        )

    cols_maestras = [
        "CODIGO",
        "CABECERA_BASE",
        "HORARIO_BASE",
        "ANUNCIO_BASE",
        "EMPRESA_BASE",
        "INTERNO_BASE",
    ]
    cols_existentes = [c for c in cols_maestras if c in df_base_clean.columns]
    df_base_sub = df_base_clean[cols_existentes].drop_duplicates(
        subset=["CODIGO"]
    )

    df_procesado = pd.merge(df_diario, df_base_sub, on="CODIGO", how="left")

    for col_orig, col_base in [
        ("CABECERA", "CABECERA_BASE"),
        ("HORARIO", "HORARIO_BASE"),
        ("ANUNCIO", "ANUNCIO_BASE"),
        ("EMPRESA", "EMPRESA_BASE"),
        ("INTERNO", "INTERNO_BASE"),
    ]:
        if col_orig not in df_procesado.columns:
            df_procesado[col_orig] = (
                df_procesado[col_base]
                if col_base in df_procesado.columns
                else ""
            )
        elif col_base in df_procesado.columns:
            m_vacio = (
                df_procesado[col_orig].isna()
                | (df_procesado[col_orig].astype(str).str.strip() == "")
            )
            df_procesado.loc[m_vacio, col_orig] = df_procesado.loc[
                m_vacio, col_base
            ]

        if col_base in df_procesado.columns:
            df_procesado = df_procesado.drop(columns=[col_base])
else:
    df_procesado = df_diario.copy()

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
]
for col in cols_orden:
    if col not in df_procesado.columns:
        df_procesado[col] = ""

df_procesado = df_procesado[cols_orden]

# ---------------------------------------------------------
# MEMORIA Y RECÁLCULO
# ---------------------------------------------------------
if "df_trabajo" not in st.session_state:
    st.session_state["df_trabajo"] = df_procesado.copy()

st.session_state["df_trabajo"][["DEMORA", "ESTADO"]] = st.session_state[
    "df_trabajo"
].apply(calcular_demora_y_estado, axis=1)

df = st.session_state["df_trabajo"]

# ---------------------------------------------------------
# ESTILOS E INTERFAZ
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
# FILTROS LATERALES
# ---------------------------------------------------------
st.sidebar.header("🔍 Filtros")

# Mantener día seleccionado en el almanaque
fecha_hoy = datetime.now(TZ_ARG).date()
if "fecha_seleccionada" not in st.session_state:
    st.session_state["fecha_seleccionada"] = fecha_hoy

fecha_sel = st.sidebar.date_input(
    "📅 Seleccionar Fecha", 
    value=st.session_state["fecha_seleccionada"],
    key="fecha_seleccionada"
)
fecha_sel_str = str(fecha_sel)

# Filtro por Empresa
empresas = ["Todas las empresas"] + sorted(
    list(
        set(
            str(e).strip()
            for e in df["EMPRESA"].dropna().unique()
            if str(e).strip() and str(e).strip().upper() != "NAN"
        )
    )
)
empresa_sel = st.sidebar.selectbox("Empresa", empresas)

# Filtro por Estado
estados = list(df["ESTADO"].unique())
estados_sel = st.sidebar.multiselect("Estado", estados, default=estados)

# Búsqueda por texto
buscar_destino = st.sidebar.text_input(
    "Anuncio / Destino", placeholder="Buscar ciudad..."
)

# ---------------------------------------------------------
# ➕ FORMULARIO PARA AGREGAR NUEVO SERVICIO (SECCIÓN 4)
# ---------------------------------------------------------
st.sidebar.markdown("---")
with st.sidebar.expander("➕ Agregar Servicio Manual", expanded=False):
    with st.form("form_nuevo_servicio", clear_on_submit=True):
        nuevo_codigo = st.text_input("Código de Servicio*")
        nuevo_cabecera = st.text_input("Cabecera / Origen")
        nuevo_horario = st.text_input("Horario Salida (HH:MM)*", placeholder="14:30")
        nuevo_anuncio = st.text_input("Anuncio / Destino")
        nuevo_empresa = st.text_input("Empresa")
        nuevo_interno = st.text_input("Interno")
        
        btn_agregar = st.form_submit_button("➕ Añadir a la lista")
        
        if btn_agregar:
            if not nuevo_codigo or not nuevo_horario:
                st.error("⚠️ El código y el horario son obligatorios.")
            else:
                nueva_fila = {
                    "FECHA": fecha_sel_str,
                    "CODIGO": str(nuevo_codigo).strip(),
                    "CABECERA": str(nuevo_cabecera).strip(),
                    "HORARIO": str(nuevo_horario).strip(),
                    "ANUNCIO": str(nuevo_anuncio).strip(),
                    "EMPRESA": str(nuevo_empresa).strip(),
                    "INTERNO": str(nuevo_interno).strip(),
                    "PLAT": "",
                    "PARTIO": "",
                    "DEMORA": 0,
                    "ESTADO": "⏳ Pendiente"
                }
                
                df_nueva_fila = pd.DataFrame([nueva_fila])
                st.session_state["df_trabajo"] = pd.concat(
                    [st.session_state["df_trabajo"], df_nueva_fila], 
                    ignore_index=True
                )
                st.success(f"✅ ¡Servicio {nuevo_codigo} añadido con éxito!")
                st.rerun()

# Aplicar Máscara de Filtros
mask = df["FECHA"].astype(str) == fecha_sel_str

if empresa_sel != "Todas las empresas":
    mask = mask & (df["EMPRESA"] == empresa_sel)

if estados_sel:
    mask = mask & (df["ESTADO"].isin(estados_sel))

if buscar_destino:
    mask = mask & (
        df["ANUNCIO"].str.contains(buscar_destino, case=False, na=False)
    )

df_filtrado = df[mask]

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
# TABLA INTERACTIVA CON ALERTAS VISUALES (SECCIÓN 2)
# ---------------------------------------------------------
col_sub, col_btn = st.columns([3, 1])
with col_sub:
    st.subheader(f"📡 Despachos del día: {fecha_sel_str}")
    st.caption(
        "💡 Puedes modificar registros o agregar nuevos. Las demoradas figuran en rojo y las puntuales en verde."
    )

with col_btn:
    csv = df_filtrado.to_csv(index=False).encode("utf-8")
    st.download_button(
        "📥 Exportar CSV",
        data=csv,
        file_name=f"tablero_flecha_{fecha_sel_str}.csv",
        mime="text/csv",
    )

# Función de resaltado/alertas visuales
def aplicar_colores(row):
    estado = row.get("ESTADO", "")
    if estado == "🔴 Demorado":
        return ["background-color: rgba(239, 68, 68, 0.25); color: #ff9999;"] * len(row)
    elif estado == "🟢 En Horario":
        return ["background-color: rgba(34, 197, 94, 0.2); color: #99ffbb;"] * len(row)
    return [""] * len(row)

df_estilizado = df_filtrado.style.apply(aplicar_colores, axis=1)

# Editor interactivo
df_editado = st.data_editor(
    df_estilizado,
    use_container_width=True,
    height=420,
    num_rows="dynamic",
    disabled=["DEMORA", "ESTADO"],
    column_config={
        "FECHA": st.column_config.TextColumn("FECHA"),
        "DEMORA": st.column_config.NumberColumn(
            "DEMORA (min)", format="%d min"
        ),
        "ESTADO": st.column_config.TextColumn("ESTADO"),
    },
    key="editor_tabla",
)

# Sincronización en tiempo real
if st.session_state.get("editor_tabla"):
    for idx, cambios in st.session_state["editor_tabla"][
        "edited_rows"
    ].items():
        indice_real = df_filtrado.index[idx]
        for campo, val in cambios.items():
            st.session_state["df_trabajo"].at[indice_real, campo] = str(val)

# ---------------------------------------------------------
# BOTONES DE ACCIÓN (GUARDAR Y CIERRE DE PLANILLA)
# ---------------------------------------------------------
st.markdown("---")
btn_col1, btn_col2 = st.columns(2)

with btn_col1:
    if st.button("💾 Guardar Cambios del Día", type="primary", use_container_width=True):
        try:
            gc = obtener_cliente_gspread()
            sh = gc.open_by_key(ID_SHEET).worksheet("plantilla_partidas")

            df_a_enviar = st.session_state["df_trabajo"].copy().fillna("")
            matriz_datos = [df_a_enviar.columns.tolist()] + df_a_enviar.astype(str).values.tolist()

            sh.clear()
            sh.update(range_name="A1", values=matriz_datos)

            st.success("✅ ¡Cambios guardados con éxito en la plantilla!")
            st.cache_data.clear()
            st.rerun()
        except Exception as err:
            st.error(f"❌ Error al guardar en Google Sheets: {err}")

with btn_col2:
    if st.button("🔒 Cierre de Planilla (Archivar Día)", type="secondary", use_container_width=True):
        try:
            gc = obtener_cliente_gspread()
            spreadsheet = gc.open_by_key(ID_SHEET)

            # 1. Obtener o crear pestaña historico_partidas
            try:
                sh_historico = spreadsheet.worksheet("historico_partidas")
            except gspread.WorksheetNotFound:
                sh_historico = spreadsheet.add_worksheet(title="historico_partidas", rows=1000, cols=15)
                sh_historico.append_row(cols_orden)

            # 2. Tomar datos actuales calculados
            df_cierre = st.session_state["df_trabajo"].copy().fillna("")
            registros_historico = df_cierre.astype(str).values.tolist()

            # 3. Anexar al final de historico_partidas
            if registros_historico:
                sh_historico.append_rows(registros_historico)

            # 4. Mantener la estructura base de CODIGOS para el día siguiente
            sh_plantilla = spreadsheet.worksheet("plantilla_partidas")
            df_limpio = df_cierre.copy()
            
            # Avanzar fecha 1 día según hora Argentina
            manana_date = datetime.now(TZ_ARG).date() + timedelta(days=1)
            manana_str = str(manana_date)
            
            # Mantener CÓDIGOS y datos base, reseteando solo operativas
            df_limpio["FECHA"] = manana_str
            df_limpio["PLAT"] = ""
            df_limpio["PARTIO"] = ""
            df_limpio["DEMORA"] = 0
            df_limpio["ESTADO"] = "⏳ Pendiente"

            matriz_limpia = [df_limpio.columns.tolist()] + df_limpio.astype(str).values.tolist()
            sh_plantilla.clear()
            sh_plantilla.update(range_name="A1", values=matriz_limpia)

            # Actualizar la fecha seleccionada a mañana para acompañar la vista
            st.session_state["fecha_seleccionada"] = manana_date

            st.success("🎉 ¡Cierre de planilla exitoso! Se archivó el historial y se mantuvieron los códigos base para mañana.")
            st.cache_data.clear()
            st.session_state.pop("df_trabajo", None)
            st.rerun()
        except Exception as err:
            st.error(f"❌ Error durante el Cierre de Planilla: {err}")