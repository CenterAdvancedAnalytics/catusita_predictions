import pandas as pd
import numpy as np
import yfinance as yf
import math
from typing import Tuple, Optional

class DataProcessor:
    def __init__(self, path: str):
        self.path = path
        self.results_models_comparison = None
        self.df_inventory = None
        self.tipo_de_cambio_df = None
        self.df_products = None
        self.back_order = None
        self.closing_prices = None
        self.long_format = None
        self.merged_df_tc_final = None
        self.df_merged = None
        self.result_precio = None
        self.margin_result = None
        self.df1_final = None
        self.dffinal2 = None

    def load_data(self) -> None:
        self.results_models_comparison = pd.read_csv(f"{self.path}/data/cleaned/predictions.csv")
        self.df_inventory = pd.read_excel(f"{self.path}/data/raw/catusita/inventory.xlsx")
        self.tipo_de_cambio_df = pd.read_excel(f"{self.path}/data/raw/catusita/saldo de todo 04.11.2024.2.xls", skiprows=2)
        self.df_products = pd.read_csv(f"{self.path}/data/process/catusita_consolidated.csv")
        try:
            self.back_order = pd.read_excel(f"{self.path}/data/raw/catusita/backorder12_12.xlsx")
        except FileNotFoundError:
            self.back_order = pd.DataFrame()

    def preprocess_exchange_rates(self) -> None:
        self.tipo_de_cambio_df = self.tipo_de_cambio_df[['Código','Mnd','Fob','Ult. Fecha','Ult. Compra']]
        self.tipo_de_cambio_df.columns = ['codigo', 'moneda', 'monto', 'ultima_fecha', 'ultima_compra']
        self.tipo_de_cambio_df = self.tipo_de_cambio_df.copy()
        self.tipo_de_cambio_df['codigo'] = self.tipo_de_cambio_df['codigo'].astype(str)
        self.tipo_de_cambio_df = self.tipo_de_cambio_df.dropna(subset=['ultima_fecha'])
        self.tipo_de_cambio_df['codigo'] = self.tipo_de_cambio_df['codigo'].str.lower()
        self.tipo_de_cambio_df = self.tipo_de_cambio_df[self.tipo_de_cambio_df['ultima_fecha'].notna()]
        self.tipo_de_cambio_df['ultima_fecha'] = pd.to_datetime(self.tipo_de_cambio_df['ultima_fecha'], errors='coerce')

    def get_currency_data(self) -> None:
        start = self.tipo_de_cambio_df['ultima_fecha'].min().date()
        end = self.tipo_de_cambio_df['ultima_fecha'].max().date()
        currency_pairs = ['PENUSD=X', 'EURUSD=X', 'JPYUSD=X', 'GBPUSD=X']
        data = yf.download(currency_pairs, start=start, end=end)
        self.closing_prices = data['Close']
        self.closing_prices.columns = [col.split('.')[0] for col in self.closing_prices.columns]

    def process_currency_data(self) -> None:
        self.long_format = self.closing_prices.reset_index().melt(id_vars='Date', var_name='Currency Pair', value_name='Closing Price')
        self.long_format['Currency Pair'] = self.long_format['Currency Pair'].str.replace('=X', '', regex=False)
        self.long_format = self.long_format.dropna(subset=['Closing Price'])
        
        full_date_range = pd.date_range(start=self.long_format['Date'].min(), end=self.long_format['Date'].max(), freq='D')
        currency_pairs = self.long_format['Currency Pair'].unique()
        complete_index = pd.MultiIndex.from_product([full_date_range, currency_pairs], names=['Date', 'Currency Pair'])
        df_full = pd.DataFrame(index=complete_index).reset_index()
        
        self.long_format = df_full.merge(self.long_format, on=['Date', 'Currency Pair'], how='left')
        self.long_format['Closing Price'] = self.long_format.groupby('Currency Pair')['Closing Price'].fillna(method='ffill')
        self.long_format = self.long_format.rename(columns={'Closing Price': 'tc'})

    def merge_exchange_rates(self) -> None:
        merged_df_tc = pd.merge(self.tipo_de_cambio_df, self.long_format, left_on='ultima_fecha', right_on='Date', how='left')
        merged_df_tc['monto'] = pd.to_numeric(merged_df_tc['monto'], errors='coerce')
        merged_df_tc['tc'] = pd.to_numeric(merged_df_tc['tc'], errors='coerce')
        
        def convert_to_usd(row):
            if pd.isna(row['Currency Pair']) or row['moneda'] == 'USD':
                return row['monto']
            currency_pair_map = {'SOL': 'PENUSD', 'EUR': 'EURUSD', 'JPY': 'JPYUSD', 'GBP': 'GBPUSD'}
            if row['moneda'] in currency_pair_map and row['Currency Pair'] == currency_pair_map[row['moneda']]:
                return row['monto'] / row['tc'] if row['moneda'] == 'SOL' else row['monto'] * row['tc']
            return 0

        merged_df_tc['monto_usd'] = merged_df_tc.apply(convert_to_usd, axis=1)
        merged_df_tc = merged_df_tc[merged_df_tc['monto_usd'] != 0]
        self.merged_df_tc_final = merged_df_tc[['codigo', 'ultima_fecha', 'monto_usd', 'ultima_compra']]
        self.merged_df_tc_final = self.merged_df_tc_final[self.merged_df_tc_final['monto_usd'].notna()]

    def process_inventory(self) -> None:
        self.df_inventory = self.df_inventory.copy()
        self.df_inventory.columns = ['cia', 'date', 'codigo', 'descripcion', 'um', 'stock']
        
        self.df_inventory = self.df_inventory[
            (self.df_inventory['date'] != 'Periodo') & 
            (self.df_inventory['date'].notna())
        ]

        self.df_inventory['date'] = pd.to_datetime(self.df_inventory['date'], format='%d/%m/%Y')
        max_date = self.results_models_comparison['date'].max()
        self.df_inventory = self.df_inventory[
            (self.df_inventory['date'] == max_date) & 
            (self.df_inventory['codigo'].notna())
        ]
        self.df_inventory.loc[:, 'codigo'] = self.df_inventory['codigo'].str.lower()

    def merge_dataframes(self) -> None:
        self.df_merged = self.results_models_comparison.copy()
        self.df_merged = self.df_merged.rename(columns={'sku':'articulo'})
        self.df_merged = self.df_merged.merge(
            self.df_products[['articulo', 'fuente_suministro','lt']].drop_duplicates(), 
            how='left', 
            on='articulo'
        )
        self.df_merged['date'] = pd.to_datetime(self.df_merged['date'])
        self.df_inventory['date'] = pd.to_datetime(self.df_inventory['date'])
        self.df_merged = self.df_merged.merge(
            self.df_inventory[['codigo', 'stock', 'date']].drop_duplicates(), 
            how='left', 
            left_on=['articulo', 'date'], 
            right_on=['codigo', 'date']
        )
        self.df_merged['stock'] = self.df_merged['stock'].fillna(0)
        self.df_merged = self.df_merged.drop(columns='codigo')

    def calculate_risk(self) -> None:
        self.df_merged['index_riesgo'] = self.df_merged['stock'] / (self.df_merged['caa'] / self.df_merged['lt_x'])
        self.df_merged['riesgo'] = pd.cut(
            self.df_merged['index_riesgo'], 
            bins=[-float('inf'), 1, 1.2, 1.5, float('inf')],
            labels=['Rojo', 'Naranja', 'Amarillo', 'Verde'], 
            right=False
        )

    def process_prices(self) -> None:
        df_precio = self.df_products[['articulo', 'cantidad', 'venta_pen', 'fecha']].copy()
        df_precio['fecha'] = pd.to_datetime(df_precio['fecha'], errors='coerce')
        df_precio = df_precio[df_precio['fecha'].dt.year == 2024]
        df_precio['precio'] = df_precio['venta_pen'] / df_precio['cantidad']
        self.result_precio = df_precio.groupby('articulo').agg(precio=('precio', 'mean')).reset_index()

    def calculate_margin(self) -> None:
        df_margen = self.df_products[['articulo', 'costo', 'venta_pen', 'fecha']].copy()
        df_margen['fecha'] = pd.to_datetime(df_margen['fecha'], errors='coerce')
        df_margen = df_margen[df_margen['fecha'].dt.year == 2024]
        df_margen['margen'] = df_margen['venta_pen'] / df_margen['costo'] - 1
        self.margin_result = df_margen.groupby('articulo').agg(
            total_venta_pen=('venta_pen', 'sum'),
            mean_margen=('margen', 'mean')
        ).reset_index().sort_values(by='total_venta_pen', ascending=False)

    def create_df1_final(self) -> None:
        df1 = self.df_merged[['fuente_suministro', 'date', 'articulo','real', 'catusita', 'caa','lt_x']].copy()
        df1 = df1.rename(columns={'catusita': 'venta_sin_recomendacion', 'caa': 'venta_con_recomendacion'})
        self.df1_final = df1.merge(self.result_precio, how='left', on='articulo')
        self.df1_final = self.df1_final[['fuente_suministro', 'date', 'articulo', 'venta_sin_recomendacion', 'venta_con_recomendacion','real', 'precio','lt_x']]
        
        self.df1_final['ingreso_sin_recomendacion'] = np.where(
            self.df1_final['venta_sin_recomendacion'] < self.df1_final['real'],
            self.df1_final['venta_sin_recomendacion'] * self.df1_final['precio'],
            self.df1_final['real'] * self.df1_final['precio']
        )
        
        self.df1_final['venta_con_recomendacion'] = np.where(
            self.df1_final['venta_con_recomendacion'] < self.df1_final['real'],
            self.df1_final['venta_con_recomendacion'] * self.df1_final['precio'],
            self.df1_final['real'] * self.df1_final['precio']
        )
        
        self.df1_final['ingreso_sin_recomendacion_ajustado'] = self.df1_final['ingreso_sin_recomendacion'] / (self.df1_final['lt_x'] * 0.83)
        self.df1_final['ingreso_con_recomendación_ajustado'] = self.df1_final['venta_con_recomendacion'] / (self.df1_final['lt_x'] * 0.83)
        
        penusd_tc = self.long_format[self.long_format['Currency Pair'] == 'PENUSD'].groupby('Date')['tc'].last().reset_index()
        self.df1_final = self.df1_final.merge(penusd_tc, how='left', left_on='date', right_on='Date')
        self.df1_final['tc'] = 1/self.df1_final['tc']
        self.df1_final['ingreso_usd_sin_recomendacion'] = self.df1_final['ingreso_sin_recomendacion_ajustado'] / self.df1_final['tc']
        self.df1_final['ingreso_usd_con_recomendacion'] = self.df1_final['ingreso_con_recomendación_ajustado'] / self.df1_final['tc']
        self.df1_final = self.df1_final[['fuente_suministro', 'date', 'articulo', 'lt_x', 'ingreso_usd_sin_recomendacion', 'ingreso_usd_con_recomendacion', 'tc']]
        self.df1_final = self.df1_final.drop_duplicates()

    def create_final_dataframe(self) -> None:
        last_date = self.df_merged['date'].max()
        df_merged_last = self.df_merged[self.df_merged['date'] == last_date].copy()
        
        df_merged_last['demanda_mensual'] = df_merged_last['caa'] / df_merged_last['lt_x']
        self.dffinal2 = df_merged_last[['articulo', 'stock', 'caa', 'demanda_mensual', 'corr_sd', 'index_riesgo', 'riesgo', 'lt_x']]
        self.dffinal2 = self.dffinal2.copy()
        self.dffinal2['meses_proteccion'] = self.dffinal2['corr_sd'] / self.dffinal2['demanda_mensual']
        self.dffinal2 = self.dffinal2[['articulo', 'stock', 'caa', 'demanda_mensual', 'meses_proteccion', 'index_riesgo', 'riesgo', 'lt_x']]
        self.dffinal2 = self.dffinal2.merge(self.margin_result[['articulo', 'mean_margen']], how='left', on='articulo')
        self.dffinal2 = self.dffinal2.merge(self.merged_df_tc_final, how='left', left_on='articulo', right_on='codigo')

    def add_compra_real(self) -> None:
        df_predicciones = pd.read_csv(f"{self.path}/data/cleaned/predictions.csv")
        df_inventory2 = pd.read_excel(f"{self.path}/data/raw/catusita/inventory.xlsx")
        
        df_predicciones = df_predicciones.rename(columns={'sku': 'articulo'})
        
        df_inventory2 = df_inventory2[
            (df_inventory2['FECHA AL'] != 'Periodo') & 
            (df_inventory2['FECHA AL'].notna())
        ]
        
        df_inventory2['FECHA AL'] = pd.to_datetime(df_inventory2['FECHA AL'], format='%d/%m/%Y')
        max_date = df_inventory2['FECHA AL'].max()
        df_inventory2 = df_inventory2[df_inventory2['FECHA AL'] == max_date]
        df_inventory2['FECHA AL'] = df_inventory2['FECHA AL'].dt.strftime('%d/%m/%Y')
        df_inventory2['CODIGO'] = df_inventory2['CODIGO'].str.lower()
        
        df_predicciones['date'] = pd.to_datetime(df_predicciones['date'], format='%Y-%m-%d')
        df_predicciones['date'] = df_predicciones['date'].dt.strftime('%d/%m/%Y')
        
        merged_df = df_predicciones.merge(
            df_inventory2[['FECHA AL', 'CODIGO', 'STOCK']], 
            how='left', 
            left_on=['articulo'], 
            right_on=['CODIGO']
        )
        
        merged_df['STOCK'] = merged_df['STOCK'].fillna(0)
        
        if not self.back_order.empty:
            merged_df = merged_df.merge(self.back_order, how='left', on='articulo')
            merged_df['backorder'] = merged_df['backorder'].fillna(0)
        else:
            merged_df['backorder'] = 0
        
        merged_df['sobrante'] = np.maximum(merged_df['STOCK'] + merged_df['backorder'] - merged_df['caa_lt'], 0)
        merged_df['nueva_compra_sugerida'] = np.maximum(merged_df['caa'] - merged_df['sobrante'], 0)
        merged_df['nueva_compra_sugerida'] = np.ceil(merged_df['nueva_compra_sugerida']).astype(int)
        
        merge_columns = merged_df[['articulo', 'nueva_compra_sugerida', 'caa', 'backorder']].copy()
        
        self.dffinal2 = self.dffinal2.merge(merge_columns, how='left', on='articulo')
        self.dffinal2['compra_sugerida'] = self.dffinal2['nueva_compra_sugerida'].fillna(0)
        self.dffinal2['backorder'] = self.dffinal2['backorder'].fillna(0)
        
        mask = self.dffinal2['demanda_mensual'] != 0
        self.dffinal2.loc[mask, 'meses_proteccion'] = (
            self.dffinal2.loc[mask, 'meses_proteccion'] * 
            (self.dffinal2.loc[mask, 'compra_sugerida'] / self.dffinal2.loc[mask, 'demanda_mensual'])
        )
        
        columns_to_drop = ['codigo', 'nueva_compra_sugerida', 'caa']
        for col in columns_to_drop:
            if col in self.dffinal2.columns:
                self.dffinal2 = self.dffinal2.drop(columns=[col])

    def finalize_processing(self) -> None:
        self.dffinal2 = self.dffinal2.rename(columns={'caa_x': 'compras_recomendadas'})
        self.dffinal2 = self.dffinal2.drop_duplicates()
        self.dffinal2['compras_recomendadas'] = self.dffinal2['compras_recomendadas'].apply(lambda x: math.ceil(x / 50) * 50)
        self.dffinal2['costo_compra'] = self.dffinal2['monto_usd'] * self.dffinal2['compras_recomendadas']

        df1_final_filled = self.df1_final.fillna(0)
        df1_final_grouped = df1_final_filled.groupby(['articulo', 'fuente_suministro']).agg({
            'ingreso_usd_sin_recomendacion': 'sum',
            'ingreso_usd_con_recomendacion': 'sum'
        }).reset_index()

        self.dffinal2 = self.dffinal2.merge(
            df1_final_grouped[['articulo', 'fuente_suministro']], 
            how='left', 
            on='articulo'
        )

        df1_final_grouped['ganancia_oportunidad'] = (
            df1_final_grouped['ingreso_usd_con_recomendacion'] - 
            df1_final_grouped['ingreso_usd_sin_recomendacion']
        )

        df1_final_grouped_fs = df1_final_grouped.groupby(['fuente_suministro']).agg({
            'ganancia_oportunidad': 'sum'
        }).reset_index()

        df1_final_grouped_fs = df1_final_grouped_fs.sort_values(
            by='ganancia_oportunidad', 
            ascending=False
        ).reset_index(drop=True)
        
        df1_final_grouped_fs['hierarchy'] = df1_final_grouped_fs.index + 1

        self.dffinal2 = self.dffinal2.merge(
            df1_final_grouped_fs[['fuente_suministro', 'hierarchy']], 
            how='left', 
            on='fuente_suministro'
        )

        self.dffinal2 = self.dffinal2.sort_values(by='hierarchy')
        self.dffinal2 = self.dffinal2[[
            'articulo','stock','compras_recomendadas','demanda_mensual','meses_proteccion',
            'index_riesgo','riesgo','lt_x','mean_margen','ultima_fecha','monto_usd',
            'ultima_compra','costo_compra','fuente_suministro','hierarchy','backorder'
        ]]

    def process_all(self) -> None:
        self.load_data()
        self.preprocess_exchange_rates()
        self.get_currency_data()
        self.process_currency_data()
        self.merge_exchange_rates()
        self.process_inventory()
        self.merge_dataframes()
        self.calculate_risk()
        self.process_prices()
        self.calculate_margin()
        self.create_df1_final()
        self.create_final_dataframe()
        self.add_compra_real()
        self.finalize_processing()

if __name__ == "__main__":
    path = 'C:/Users/Christopher/OneDrive/Documentos/GitHub/catusita_revamp'
    processor = DataProcessor(path)
    processor.process_all()