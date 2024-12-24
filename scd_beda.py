import pandas as pd
import osmnx as ox
import networkx as nx
import folium
from streamlit_folium import st_folium
import streamlit as st
import gurobipy as gp
from gurobipy import GRB
print(ox.__version__)


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
    # payload_column = st.sidebar.selectbox("Pilih Kolom untuk Payload", site_data.columns)
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
    S = st.sidebar.number_input("Jumlah Home Base", min_value=1, max_value=25, value=3)  # Total Home Base
    scd_counts = []
    for j in range(S):
        scd_count = st.sidebar.number_input(f"Jumlah SCD untuk Home Base {j+1}", min_value=1, max_value=10, value=1)
        scd_counts.append(scd_count)

    # K = st.sidebar.number_input("Jumlah SCD per Home Base", min_value=1, max_value=10, value=1) 

    # Parameter lainnya
    B = site_data[bbt_time_column].tolist()
    D = site_data[pln_down_time_column].tolist()
    P = site_data['Prioritize'].tolist()
    T = site_data[[f'T_SCD_{i + 1}' for i in range(S)]].values  # Matrix waktu dari SCD ke site

    # Membuat model Gurobi
    model = gp.Model("Optimasi_Alokasi_Genset")

    # Variabel keputusan
    # z = model.addVars(S, K, N, vtype=GRB.BINARY, name="z")
    z = {}
    for j in range(S):
        for k in range(scd_counts[j]):
            for i in range(N):
                z[j, k, i] = model.addVar(vtype=GRB.BINARY, name=f"z_{j}_{k}_{i}")

    y = model.addVars(N, vtype=GRB.BINARY, name="y")

    P = [0 if pd.isnull(x) or x in [float('inf'), float('-inf')] else x for x in P]  

    # Fungsi tujuan: Maksimalkan total prioritas site yang menerima alokasi genset
    model.setObjective(gp.quicksum(y[i] * P[i] for i in range(N)), GRB.MAXIMIZE)

   # Kendala 1: Site hanya menerima satu genset
    for i in range(N):
        model.addConstr(gp.quicksum(z[j, k, i] for j in range(S) for k in range(scd_counts[j])) <= 1, name=f"site_constraint_{i}")


    # Kendala 2: Genset hanya dialokasikan ke satu site
    for j in range(S):
      for k in range(scd_counts[j]):
          model.addConstr(gp.quicksum(z[j, k, i] for i in range(N)) <= 1, name=f"genset_constraint_{j}_{k}")


    # Kendala 3: y_i = 1 jika ada genset dialokasikan ke site i
    for i in range(N):
        model.addConstr(y[i] <= gp.quicksum(z[j, k, i] for j in range(S) for k in range(scd_counts[j])), name=f"y_constraint_{i}")


    # Kendala 4: Genset dialokasikan jika PLN Down Time > Bbt Time
    for j in range(S):
        for k in range(scd_counts[j]):
            for i in range(N):
                if D[i] <= B[i]:
                    model.addConstr(z[j, k, i] == 0, name=f"pln_constraint_{j}_{k}_{i}")

    # Kendala 5: Alokasi genset hanya ke site dengan T_SCD <= Pln Down Time dan <= Bbt Time
    for j in range(S):
        for k in range(scd_counts[j]):
            for i in range(N):
                if T[i, j] > D[i] or T[i, j] > B[i]:  # Tambahkan kondisi T_SCD > Bbt Time
                    model.addConstr(z[j, k, i] == 0, name=f"time_constraint_{j}_{k}_{i}")

    # Optimalisasi model
    model.optimize()

    # Array untuk menyimpan hasil alokasi
    selected_sites = []

    # Proses hasil dari model jika solusi optimal ditemukan
    if model.status == GRB.OPTIMAL:
        st.success("Solusi Optimal Ditemukan!")
        for i in range(N):
            if y[i].x > 0.5:
                allocated_gensets = [
                    (j, k) for j in range(S) for k in range(scd_counts[j]) if z[j, k, i].x > 0.5
                ]
                for j, k in allocated_gensets:
                    selected_sites.append({
                        "site_id": site_data['site_id'][i],
                        "scd_id": j + 1,
                        "scd_index": k + 1
                    })
                    st.write(
                        f"Site {site_data['site_id'][i]} disuplai oleh T_SCD_{j + 1}, SCD Index {k + 1}"
                    )
    else:
        st.warning("Tidak ada solusi optimal.")


    # Menampilkan hasil alokasi
    st.write("Hasil Alokasi Genset:", selected_sites)

    # Membaca data SCD dan site
    scd_data = scd_data[scd_data['to_name'] == to_filter]
    print(scd_data)

    filtered_data = site_data[site_data['site_id'].isin([alloc['site_id'] for alloc in selected_sites])]

    # Menyiapkan peta
    map_center = [0.5, 102.7]
    mymap = folium.Map(location=map_center, zoom_start=9)

    # Menambahkan marker untuk site yang terpilih
    for _, row in filtered_data.iterrows():
        folium.Marker(
            location=[row['lat'], row['long']],
            popup=f"Site ID: {row['site_id']}",
            icon=folium.Icon(color='blue', icon='info-sign')
        ).add_to(mymap)

    # Menambahkan marker untuk SCD
    for _, row in scd_data.iterrows():
        folium.Marker(
            location=[row['lat'], row['long']],
            popup=f"SCD: {row['Home base']} (SCD-{row['SCD']})",
            icon=folium.Icon(color='red', icon='cloud')
        ).add_to(mymap)

    # Membuat graph jalan dengan OSMN
    north = float(0.8)
    south = float(0.3)
    east = float(102.9)
    west = float(102.5)

    G = ox.graph_from_bbox(north=north, south=south, east=east, west=west, network_type="drive")
    # G = ox.graph_from_bbox(north=0.8, south=0.3, east=102.9, west=102.5, network_type="drive")


    # Menambahkan rute ke peta
    for alloc in selected_sites:
      # Filter data site
      site_filtered = site_data[site_data['site_id'] == alloc['site_id']]
      if not site_filtered.empty:
          site = site_filtered.iloc[0]
      else:
          st.warning(f"Tidak ada data untuk Site ID: {alloc['site_id']}")
          continue

      # Filter data SCD
      scd_filtered = scd_data[scd_data['SCD'] == alloc['scd_id']]
      print(scd_data['SCD'] )
      print(alloc['scd_id'])
      print(alloc)
      print(scd_data)
      if not scd_filtered.empty:
          scd = scd_filtered.iloc[0]
      else:
          st.warning(f"Tidak ada data untuk SCD ID: {alloc['scd_id']}")
          continue

      # Tambahkan PolyLine untuk rute
      folium.PolyLine(
          locations=[[scd['lat'], scd['long']], [site['lat'], site['long']]],
          color='green',
          weight=2.5,
          opacity=0.8,
          popup=f"Route: SCD-{scd['SCD']} to {site['site_id']}"
      ).add_to(mymap)

    st.title("Visualisasi Rute Alokasi Genset")
    st_folium(mymap, width=1000, height=600)

else:
    st.warning("Silakan unggah file terlebih dahulu.")
