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
# CARGA DE DATOS DE GOOGLE SHEETS
# ---------------------------------------------------------
try:
    df_diario = cargar_pestana("plantilla_partidas")  # Pestaña acumulativa/plantilla
    df_base = cargar_pestana("Base_Servicios")  # Base maestra copiada de la API
except Exception as e:
    st.error(f"❌ Error al conectar con Google Sheets: {e}")
    st.stop()

# Garantizar columnas requeridas en el orden deseado
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

if not df_diario.empty:
    df_diario.columns = df_diario.columns.str.upper()
    for col in cols_orden:
        if col not in df_diario.columns:
            df_diario[col] = ""
    df_diario = df_diario[cols_orden].copy()
else:
    df_diario = pd.DataFrame(columns=cols_orden)

# ---------------------------------------------------------
# MEMORIA Y RECÁLCULO
# ---------------------------------------------------------
if "df_trabajo" not in st.session_state:
    st.session_state["df_trabajo"] = df_diario.copy()

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
estados = list(df["ESTADO"].unique()) if not df.empty else []
estados_sel = st.sidebar.multiselect("Estado", estados, default=estados)

# Búsqueda por texto
buscar_destino = st.sidebar.text_input(
    "Anuncio / Destino", placeholder="Buscar ciudad..."
)

# ---------------------------------------------------------
# FORMULARIO PARA AGREGAR NUEVO SERVICIO INDIVIDUAL
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
mask = df["FECHA"].astype(str) == fecha_sel_str if not df.empty else pd.Series(dtype=bool)

if empresa_sel != "Todas las empresas" and not df.empty:
    mask = mask & (df["EMPRESA"] == empresa_sel)

if estados_sel and not df.empty:
    mask = mask & (df["ESTADO"].isin(estados_sel))

if buscar_destino and not df.empty:
    mask = mask & (
        df["ANUNCIO"].str.contains(buscar_destino, case=False, na=False)
    )

df_filtrado = df[mask] if not df.empty else pd.DataFrame(columns=cols_orden)

# ---------------------------------------------------------
# GENERACIÓN DE DÍA BASADO EN CÓDIGOS DE PLANTILLA + VLOOKUP BASE_SERVICIOS
# ---------------------------------------------------------
if df_filtrado.empty:
    st.info(f"📅 No hay servicios asignados para la fecha **{fecha_sel_str}**.")
    col_cargar, _ = st.columns([2, 1])
    with col_cargar:
        if st.button(f"📥 Cargar CÓDIGOS del {fecha_sel_str} (y cruzar con API / Base Servicios)", type="primary", use_container_width=True):
            if df_diario.empty:
                st.error("❌ No se encontraron códigos en la pestaña 'plantilla_partidas'.")
            else:
                # 1. Obtener la lista única de CODIGO de plantilla_partidas
                df_codigos_base = df_diario[["CODIGO"]].drop_duplicates().copy()
                df_codigos_base = df_codigos_base[df_codigos_base["CODIGO"].astype(str).str.strip() != ""]

                # 2. Asignarle la fecha del almanaque y columnas vacías
                df_codigos_base["FECHA"] = fecha_sel_str
                df_codigos_base["PLAT"] = ""
                df_codigos_base["PARTIO"] = ""
                df_codigos_base["DEMORA"] = 0
                df_codigos_base["ESTADO"] = "⏳ Pendiente"

                # 3. Cruzar con Base_Servicios (que actualizás con la API) si tiene datos
                if not df_base.empty:
                    renombres_base = {
                        "Código": "CODIGO",
                        "Origen": "CABECERA_BASE",
                        "Se anuncia a": "ANUNCIO_BASE",
                        "Código de transportista": "EMPRESA_BASE",
                        "Interno": "INTERNO_BASE",
                    }
                    df_b_clean = df_base.rename(columns=renombres_base).copy()

                    if "Fecha salida" in df_b_clean.columns:
                        df_b_clean["HORARIO_BASE"] = (
                            pd.to_datetime(df_b_clean["Fecha salida"], errors="coerce")
                            .dt.strftime("%H:%M")
                        )

                    cols_b = ["CODIGO", "CABECERA_BASE", "HORARIO_BASE", "ANUNCIO_BASE", "EMPRESA_BASE", "INTERNO_BASE"]
                    cols_b_ex = [c for c in cols_b if c in df_b_clean.columns]
                    df_b_sub = df_b_clean[cols_b_ex].drop_duplicates(subset=["CODIGO"])

                    # Hacemos el Merge (VLOOKUP)
                    df_m = pd.merge(df_codigos_base, df_b_sub, on="CODIGO", how="left")

                    df_m["CABECERA"] = df_m.get("CABECERA_BASE", "")
                    df_m["HORARIO"] = df_m.get("HORARIO_BASE", "")
                    df_m["ANUNCIO"] = df_m.get("ANUNCIO_BASE", "")
                    df_m["EMPRESA"] = df_m.get("EMPRESA_BASE", "")
                    df_m["INTERNO"] = df_m.get("INTERNO_BASE", "")

                    cols_drop = [c for c in ["CABECERA_BASE", "HORARIO_BASE", "ANUNCIO_BASE", "EMPRESA_BASE", "INTERNO_BASE"] if c in df_m.columns]
                    df_m = df_m.drop(columns=cols_drop)
                    df_nueva_prog = df_m
                else:
                    for c in ["CABECERA", "HORARIO", "ANUNCIO", "EMPRESA", "INTERNO"]:
                        df_codigos_base[c] = ""
                    df_nueva_prog = df_codigos_base

                # Asegurar orden de columnas
                for c in cols_orden:
                    if c not in df_nueva_prog.columns:
                        df_nueva_prog[c] = ""
                df_nueva_prog = df_nueva_prog[cols_orden]

                # Anexar a la tabla de trabajo
                st.session_state["df_trabajo"] = pd.concat(
                    [st.session_state["df_trabajo"], df_nueva_prog], 
                    ignore_index=True
                )
                st.success(f"🎉 Se cargaron los códigos de la plantilla para el {fecha_sel_str} con sus datos actualizados de la API.")
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
# TABLA INTERACTIVA CON ALERTAS VISUALES
# ---------------------------------------------------------
col_sub, col_btn = st.columns([3, 1])
with col_sub:
    st.subheader(f"📡 Despachos del día: {fecha_sel_str}")
    st.caption(
        "💡 Puedes modificar registros. Las demoradas figuran en rojo y las puntuales en verde."
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

if not df_filtrado.empty:
    df_estilizado = df_filtrado.style.apply(aplicar_colores, axis=1)

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
# BOTÓN DE GUARDAR CAMBIOS
# ---------------------------------------------------------
st.markdown("---")
if st.button("💾 Guardar Cambios en Google Sheets", type="primary", use_container_width=True):
    try:
        gc = obtener_cliente_gspread()
        sh = gc.open_by_key(ID_SHEET).worksheet("plantilla_partidas")

        df_a_enviar = st.session_state["df_trabajo"].copy().fillna("")
        matriz_datos = [df_a_enviar.columns.tolist()] + df_a_enviar.astype(str).values.tolist()

        sh.clear()
        sh.update(range_name="A1", values=matriz_datos)

        st.success("✅ ¡Base de datos guardada y actualizada correctamente en Google Sheets!")
        st.cache_data.clear()
        st.rerun()
    except Exception as err:
        st.error(f"❌ Error al guardar en Google Sheets: {err}")