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

# Orden de columnas definitivo
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
# CONEXIÓN Y CARGA DE GOOGLE SHEETS
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
def cargar_pestana(nombre_o_index):
    try:
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
    except Exception:
        return pd.DataFrame()


def parsear_hora(texto_hora):
    if not texto_hora or pd.isna(texto_hora):
        return None
    match = re.search(r"(\d{1,2}):(\d{2})", str(texto_hora).strip())
    if match:
        h, m = map(int, match.groups())
        return h * 60 + m
    return None


def ordenar_por_horario(df_input):
    """Ordena el DataFrame por la columna HORARIO cronológicamente."""
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


# ---------------------------------------------------------
# CARGA Y PREPARACIÓN DE DATOS
# ---------------------------------------------------------
try:
    df_diario = cargar_pestana("plantilla_partidas")
    df_base = cargar_pestana("Base_Servicios")
    df_codigos_sheet = cargar_pestana("codigos")
except Exception as e:
    st.error(f"❌ Error al conectar con Google Sheets: {e}")
    st.stop()

# Garantizar columnas
if not df_diario.empty:
    df_diario.columns = df_diario.columns.str.upper()
    for col in cols_orden:
        if col not in df_diario.columns:
            df_diario[col] = ""
    df_diario = df_diario[cols_orden].copy()
else:
    df_diario = pd.DataFrame(columns=cols_orden)

# Estado de la Sesión
if "df_trabajo" not in st.session_state:
    st.session_state["df_trabajo"] = ordenar_por_horario(df_diario.copy())

# Recalcular demoras
if not st.session_state["df_trabajo"].empty:
    st.session_state["df_trabajo"][["DEMORA", "ESTADO"]] = st.session_state[
        "df_trabajo"
    ].apply(calcular_demora_y_estado, axis=1)

df = st.session_state["df_trabajo"]

# Normalización de Base_Servicios para búsquedas rápidas
df_b_sub = pd.DataFrame()
if not df_base.empty:
    renombres_base = {
        "Código": "CODIGO",
        "Origen": "CABECERA",
        "Se anuncia a": "ANUNCIO",
        "Código de transportista": "EMPRESA",
        "Interno": "INTERNO",
    }
    df_b_clean = df_base.rename(columns=renombres_base).copy()

    if "Fecha salida" in df_b_clean.columns:
        df_b_clean["HORARIO_BASE"] = pd.to_datetime(
            df_b_clean["Fecha salida"], errors="coerce"
        ).dt.strftime("%H:%M")

    cols_b = ["CODIGO", "CABECERA", "HORARIO_BASE", "ANUNCIO", "EMPRESA", "INTERNO"]
    cols_b_ex = [c for c in cols_b if c in df_b_clean.columns]
    df_b_sub = df_b_clean[cols_b_ex].drop_duplicates(subset=["CODIGO"])

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
# FILTROS LATERALES
# ---------------------------------------------------------
st.sidebar.header("🔍 Filtros & Opciones")

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
            for e in df["EMPRESA"].dropna().unique()
            if str(e).strip() and str(e).strip().upper() != "NAN"
        )
    )
)
empresa_sel = st.sidebar.selectbox("Empresa", empresas)

estados = list(df["ESTADO"].unique()) if not df.empty else []
estados_sel = st.sidebar.multiselect("Estado", estados, default=estados)

buscar_destino = st.sidebar.text_input(
    "Anuncio / Destino", placeholder="Buscar ciudad..."
)

# ---------------------------------------------------------
# FORMULARIO CON AUTOCOMPLETADO
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
    elif not df_b_sub.empty:
        match = df_b_sub[
            df_b_sub["CODIGO"].astype(str).str.strip()
            == cod_ingresado.strip()
        ]
        if not match.empty:
            row_m = match.iloc[0]
            st.session_state["form_manual"] = {
                "CODIGO": cod_ingresado.strip(),
                "CABECERA": str(row_m.get("CABECERA", "")),
                "HORARIO": str(row_m.get("HORARIO_BASE", "")),
                "ANUNCIO": str(row_m.get("ANUNCIO", "")),
                "EMPRESA": str(row_m.get("EMPRESA", "")),
                "INTERNO": str(row_m.get("INTERNO", "")),
            }
            st.sidebar.success("¡Datos encontrados y cargados!")
            st.rerun()
        else:
            st.sidebar.error("Código no encontrado en Base_Servicios.")
    else:
        st.sidebar.error("Base_Servicios está vacía.")

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

    btn_agregar = st.form_submit_button("➕ Añadir al día actual")

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
                [st.session_state["df_trabajo"], pd.DataFrame([nueva_fila])],
                ignore_index=True,
            )
            st.session_state["df_trabajo"] = ordenar_por_horario(df_actualizado)

            st.session_state["form_manual"] = {
                "CODIGO": "",
                "CABECERA": "",
                "HORARIO": "",
                "ANUNCIO": "",
                "EMPRESA": "",
                "INTERNO": "",
            }
            st.success(f"✅ ¡Servicio {cod_ingresado} añadido con éxito!")
            st.rerun()

# ---------------------------------------------------------
# FILTRADO Y PROGRAMACIÓN AUTOMÁTICA DEL DÍA
# ---------------------------------------------------------
mask = (
    df["FECHA"].astype(str) == fecha_sel_str
    if not df.empty
    else pd.Series(dtype=bool)
)

if empresa_sel != "Todas las empresas" and not df.empty:
    mask = mask & (df["EMPRESA"] == empresa_sel)

if estados_sel and not df.empty:
    mask = mask & (df["ESTADO"].isin(estados_sel))

if buscar_destino and not df.empty:
    mask = mask & (
        df["ANUNCIO"].str.contains(buscar_destino, case=False, na=False)
    )

df_filtrado = (
    ordenar_por_horario(df[mask].copy())
    if not df.empty
    else pd.DataFrame(columns=cols_orden)
)

# Carga Automática inicial desde la pestaña "codigos"
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
                st.error(
                    "❌ La pestaña 'codigos' está vacía o no existe en Google Sheets."
                )
            else:
                col_cod = [
                    c for c in df_codigos_sheet.columns if "CODIGO" in c.upper()
                ]
                nombre_col_cod = col_cod[0] if col_cod else df_codigos_sheet.columns[0]

                df_c_base = df_codigos_sheet[[nombre_col_cod]].copy()
                df_c_base.columns = ["CODIGO"]
                df_c_base = df_c_base[
                    df_c_base["CODIGO"].astype(str).str.strip() != ""
                ].drop_duplicates()

                df_c_base["FECHA"] = fecha_sel_str
                df_c_base["PLAT"] = ""
                df_c_base["PARTIO"] = ""
                df_c_base["DEMORA"] = 0
                df_c_base["ESTADO"] = "⏳ Pendiente"
                df_c_base["COMENTARIOS"] = ""
                df_c_base["GUARDADO"] = "NO"

                if not df_b_sub.empty:
                    df_nueva_prog = pd.merge(
                        df_c_base, df_b_sub, on="CODIGO", how="left"
                    )
                    if "HORARIO_BASE" in df_nueva_prog.columns:
                        df_nueva_prog["HORARIO"] = df_nueva_prog["HORARIO_BASE"]
                        df_nueva_prog = df_nueva_prog.drop(columns=["HORARIO_BASE"])
                else:
                    df_nueva_prog = df_c_base
                    df_nueva_prog["HORARIO"] = ""

                for c in cols_orden:
                    if c not in df_nueva_prog.columns:
                        df_nueva_prog[c] = ""
                df_nueva_prog = df_nueva_prog[cols_orden].fillna("")

                df_mezclado = pd.concat(
                    [st.session_state["df_trabajo"], df_nueva_prog],
                    ignore_index=True,
                )
                st.session_state["df_trabajo"] = ordenar_por_horario(df_mezclado)

                st.success(
                    f"🎉 ¡Día {fecha_sel_str} cargado! Podés ajustar los horarios para Retiro cuando quieras."
                )
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
# TABLA INTERACTIVA CON RESALTADO PUNTUAL EN CABECERA
# ---------------------------------------------------------
col_sub, col_btn = st.columns([3, 1])
with col_sub:
    st.subheader(f"📡 Despachos del día: {fecha_sel_str}")
    st.caption(
        "💡 **Leyenda:** Celdas en 🟡 **CABECERA** indican servicios de paso (no originan en RET) que requieren revisar horario."
    )

with col_btn:
    csv = df_filtrado.to_csv(index=False).encode("utf-8")
    st.download_button(
        "📥 Exportar CSV",
        data=csv,
        file_name=f"tablero_flecha_{fecha_sel_str}.csv",
        mime="text/csv",
    )


def aplicar_estilo_tabla(df_in):
    """
    Aplica estilos por celda/fila:
    1. Fila completa en rojo si está Demorado.
    2. Fila completa en verde si está En Horario.
    3. Celda puntual de 'CABECERA' en amarillo/naranja si NO es RET.
    """
    # DataFrame de estilos vacíos
    styles = pd.DataFrame("", index=df_in.index, columns=df_in.columns)

    for idx, row in df_in.iterrows():
        estado = str(row.get("ESTADO", ""))
        cabecera = str(row.get("CABECERA", "")).strip().upper()

        # Estilo de fila según Estado
        if estado == "🔴 Demorado":
            styles.loc[idx, :] = "background-color: rgba(239, 68, 68, 0.25); color: #ff9999;"
        elif estado == "🟢 En Horario":
            styles.loc[idx, :] = "background-color: rgba(34, 197, 94, 0.2); color: #99ffbb;"

        # Resaltado PUNTUAL en la celda CABECERA si NO es RET (Sobreescribe solo esa celda)
        if cabecera != "RET" and cabecera != "":
            styles.loc[idx, "CABECERA"] = (
                "background-color: rgba(245, 158, 11, 0.45); "
                "color: #fde047; "
                "font-weight: bold;"
            )

    return styles


if not df_filtrado.empty:
    cols_disabled = ["DEMORA", "ESTADO"]

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

    df_estilizado = df_filtrado.style.apply(aplicar_estilo_tabla, axis=None)

    df_editado = st.data_editor(
        df_estilizado,
        use_container_width=True,
        height=420,
        num_rows="dynamic",
        disabled=cols_disabled,
        column_config={
            "FECHA": st.column_config.TextColumn("FECHA"),
            "CABECERA": st.column_config.TextColumn("CABECERA"),
            "HORARIO": st.column_config.TextColumn("HORARIO (RET)"),
            "DEMORA": st.column_config.NumberColumn(
                "DEMORA (min)", format="%d min"
            ),
            "ESTADO": st.column_config.TextColumn("ESTADO"),
            "COMENTARIOS": st.column_config.TextColumn("COMENTARIOS"),
            "GUARDADO": st.column_config.TextColumn("GUARDADO"),
        },
        key="editor_tabla",
    )

    # Sincronización precisa en tiempo real
    if st.session_state.get("editor_tabla"):
        hubo_cambio_horario = False
        for idx, cambios in st.session_state["editor_tabla"][
            "edited_rows"
        ].items():
            indice_real = df_filtrado.index[idx]
            for campo, val in cambios.items():
                st.session_state["df_trabajo"].at[indice_real, campo] = str(val)
                if campo == "HORARIO":
                    hubo_cambio_horario = True

        if hubo_cambio_horario:
            st.session_state["df_trabajo"] = ordenar_por_horario(
                st.session_state["df_trabajo"]
            )
            st.rerun()

# ---------------------------------------------------------
# BOTÓN DE GUARDAR CAMBIOS
# ---------------------------------------------------------
st.markdown("---")
if st.button(
    "💾 Guardar Cambios en Google Sheets",
    type="primary",
    use_container_width=True,
):
    try:
        gc = obtener_cliente_gspread()
        sh = gc.open_by_key(ID_SHEET).worksheet("plantilla_partidas")

        mask_guardar = (
            st.session_state["df_trabajo"]["FECHA"].astype(str) == fecha_sel_str
        )
        st.session_state["df_trabajo"].loc[mask_guardar, "GUARDADO"] = "SI"

        st.session_state["df_trabajo"] = ordenar_por_horario(
            st.session_state["df_trabajo"]
        )

        df_a_enviar = st.session_state["df_trabajo"].copy().fillna("")
        matriz_datos = [
            df_a_enviar.columns.tolist()
        ] + df_a_enviar.astype(str).values.tolist()

        sh.clear()
        sh.update(range_name="A1", values=matriz_datos)

        st.success(
            f"✅ ¡Cambios del {fecha_sel_str} guardados correctamente con resaltado en celdas de Cabecera!"
        )
        st.cache_data.clear()
        st.rerun()
    except Exception as err:
        st.error(f"❌ Error al guardar en Google Sheets: {err}")