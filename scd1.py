import pandas as pd
import osmnx as ox
import networkx as nx
import folium
from streamlit_folium import st_folium
import streamlit as st
import gurobipy as gp
from gurobipy import GRB

# Sidebar untuk input file dan pengaturan
st.sidebar.title("Pengaturan Input")
site_file = st.sidebar.file_uploader("Pilih File Site", type=["xlsx", "csv"])
scd_file = st.sidebar.file_uploader("Pilih File SCD", type=["xlsx", "csv"])

if site_file is not None and scd_file is not None:
    # Membaca data dari file yang diunggah
    site_data = pd.read_excel(site_file) if site_file.name.endswith('.xlsx') else pd.read_csv(site_file)
    scd_data = pd.read_excel(scd_file) if scd_file.name.endswith('.xlsx') else pd.read_csv(scd_file)

    # Memilih kolom yang relevan dari file Site
    to_column = st.sidebar.selectbox("Pilih Kolom untuk TO", site_data.columns)
    class_column = st.sidebar.selectbox("Pilih Kolom untuk Kelas Site", site_data.columns)
    anakan_column = st.sidebar.selectbox("Pilih Kolom untuk Anakan", site_data.columns)
    payload_column = st.sidebar.selectbox("Pilih Kolom untuk Payload", site_data.columns)
    bbt_time_column = st.sidebar.selectbox("Pilih Kolom untuk BBT Time", site_data.columns)
    pln_down_time_column = st.sidebar.selectbox("Pilih Kolom untuk PLN Down Time", site_data.columns)

    # Filter berdasarkan TO jika diperlukan
    to_filter = st.sidebar.selectbox("Pilih TO untuk Filter", site_data[to_column].unique())
    site_data = site_data[site_data[to_column] == to_filter]

    # Menentukan nilai bobot dan parameter lainnya
    bobot_simul_anakan = st.sidebar.slider("Bobot Anakan", 0.0, 1.0, 0.3)
    bobot_kelas = st.sidebar.slider("Bobot Kelas", 0.0, 1.0, 0.7)

    # Kolom yang digunakan untuk menghitung prioritas
    site_data['Class_Priority'] = site_data[class_column].map({'Diamond': 5, 'Gold': 3, 'Platinum': 4, 'Silver': 2, 'Bronze': 1})
    
    # Normalisasi dan Prioritas
    def normalize(value, min_val, max_val, scale_min=1, scale_max=5):
        return scale_min + (value - min_val) * (scale_max - scale_min) / (max_val - min_val)

    site_data['Prioritize'] = (
        bobot_simul_anakan * normalize(site_data[anakan_column], site_data[anakan_column].min(), site_data[anakan_column].max()) +
        bobot_kelas * normalize(site_data['Class_Priority'], site_data['Class_Priority'].min(), site_data['Class_Priority'].max())
    )

    # Sorting berdasarkan Prioritas
    site_data = site_data.sort_values(by='Prioritize', ascending=False).reset_index(drop=True).head(100)

    # Menampilkan preview data
    st.write("Data Site yang Diupload dan Diproses", site_data)

    # Model Gurobi Setup (seperti yang Anda buat sebelumnya)
    N = len(site_data)  # Jumlah site
    S = st.sidebar.number_input("Jumlah SCD", min_value=1, max_value=10, value=8)  # Jumlah SCD
    K = st.sidebar.number_input("Jumlah Genset per SCD", min_value=1, max_value=10, value=1)  # Jumlah genset per SCD

    # Parameter lainnya
    B = site_data[bbt_time_column].tolist()
    D = site_data[pln_down_time_column].tolist()
    P = site_data['Prioritize'].tolist()
    T = site_data[[f'T_SCD_{i + 1}' for i in range(S)]].values  # Matrix waktu dari SCD ke site

    # Membuat model Gurobi
    model = gp.Model("Optimasi_Alokasi_Genset")

    # Variabel keputusan
    z = model.addVars(S, K, N, vtype=GRB.BINARY, name="z")
    y = model.addVars(N, vtype=GRB.BINARY, name="y")

    P = [0 if pd.isnull(x) or x in [float('inf'), float('-inf')] else x for x in P]  

    # Fungsi tujuan: Maksimalkan total prioritas site yang menerima alokasi genset
    model.setObjective(gp.quicksum(y[i] * P[i] for i in range(N)), GRB.MAXIMIZE)

   # Kendala 1: Site hanya menerima satu genset
    for i in range(N):
        model.addConstr(gp.quicksum(z[j, k, i] for j in range(S) for k in range(K)) <= 1, name=f"site_constraint_{i}")

    # Kendala 2: Genset hanya dialokasikan ke satu site
    for j in range(S):
        for k in range(K):
            model.addConstr(gp.quicksum(z[j, k, i] for i in range(N)) <= 1, name=f"genset_constraint_{j}_{k}")

    # Kendala 3: y_i = 1 jika ada genset dialokasikan ke site i
    for i in range(N):
        model.addConstr(y[i] <= gp.quicksum(z[j, k, i] for j in range(S) for k in range(K)), name=f"y_constraint_{i}")

    # Kendala 4: Genset dialokasikan jika PLN Down Time > Bbt Time
    for j in range(S):
        for k in range(K):
            for i in range(N):
                if D[i] <= B[i]:
                    model.addConstr(z[j, k, i] == 0, name=f"pln_constraint_{j}_{k}_{i}")

    # Kendala 5: Alokasi genset hanya ke site dengan T_SCD <= Pln Down Time dan <= Bbt Time
    for j in range(S):
        for k in range(K):
            for i in range(N):
                if T[i, j] > D[i] or T[i, j] > B[i]:  # Tambahkan kondisi T_SCD > Bbt Time
                    model.addConstr(z[j, k, i] == 0, name=f"time_constraint_{j}_{k}_{i}")


    # Menyelesaikan model
    model.optimize()

    # Array untuk menyimpan hasil alokasi
    selected_sites = []

    if model.status == GRB.OPTIMAL:
        for i in range(N):
            if y[i].x > 0.5:
                allocated_gensets = [(j, k) for j in range(S) for k in range(K) if z[j, k, i].x > 0.5]
                for j, k in allocated_gensets:
                    selected_sites.append({"site_id": site_data['site_id'][i], "scd_id": j + 1})
    
    # Menampilkan hasil alokasi
    st.write(selected_sites)

    # Menampilkan peta rute
    map_center = [0.5, 102.7]  
    mymap = folium.Map(location=map_center, zoom_start=9)

    # Menambahkan marker untuk site yang terpilih
    for _, row in site_data.iterrows():
        folium.Marker(
            location=[row['lat'], row['long']],
            popup=f"Site ID: {row['site_id']}",
            icon=folium.Icon(color='blue', icon='info-sign')
        ).add_to(mymap)

    # Menampilkan peta
    st.title("Visualisasi Rute Alokasi Genset")
    st_folium(mymap, width=1000, height=600)
else:
    st.warning("Silakan unggah file terlebih dahulu.")
