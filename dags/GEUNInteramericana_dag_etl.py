#import logging
from airflow.models import DAG
from datetime import datetime, timedelta
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.operators.python import PythonOperator
#from operators.s3_to_postgres_operator import S3ToPostgresOperator
#from airflow.providers.amazon.aws.hooks.s3 import S3Hook
import pandas as pd
from dateutil.relativedelta import relativedelta
from airflow.providers.amazon.aws.transfers.local_to_s3 import (
    LocalFilesystemToS3Operator
)

#defino un diccionario con las variables del DAG
default_args = {
    #'schedule_interval' : "@hourly",   NO VA ACA
    'start_date' : datetime(2022, 11, 4),
    'catchup' : False,
    'retries': 5,
    'owner' : 'alfredo'
    }

# defino la funcion de extraccion y generacion de los csv
def extract_csv():
    with open (f'/usr/local/airflow/include/GE_UniInteramericana.sql', 'r') as sqfile:
        query = sqfile.read() 
    hook = PostgresHook(postgres_conn_id="alkemy_db")
    #logging.info("Exporting query to file")
    df = hook.get_pandas_df(sql=query)
    df.to_csv('/usr/local/airflow/tests/GE_Interamericana_select.csv')

# defino la funcion de transformación
def transform_pandas():
    #se cargan los datasets
    df_cp = pd.read_csv('/usr/local/airflow/tests/codigos_postales.csv')
    df = pd.read_csv('/usr/local/airflow/tests/GE_Interamericana_select.csv')

    #renombro la columna 'Unnamed: 0' por 'Id'
    df = df.rename(columns = {'Unnamed: 0': 'Id'})

    df['university'] = df['university'].astype(str)
    df['university'] = df['university'].str.lower().str.replace("^-", "").str.replace("-", " ").str.replace("  ", " ")

    df['career'] = df['career'].astype(str)
    df['career'] = df['career'].str.lower().str.replace("^-", "").str.replace("-", " ").str.replace("  ", " ")

    df['last_name'] = df['last_name'].str.lower()
    new = df['last_name'].str.split("-", n = 1, expand = True)
    df['first_name']= new[0]
    df['last_name']= new[1]

    df['gender'].iloc[df['gender'] == 'M'] = 'male'
    df['gender'].iloc[df['gender'] == 'F'] = 'female'

    # fecha de nacimiento en formato YY(dos digitos)/mes(string)/dd
    df['fecha_nacimiento'] = pd.to_datetime(df['fecha_nacimiento'], format='%y/%b/%d')
    df['inscription_date'] = pd.to_datetime(df['inscription_date'], format='%Y-%m-%d')
    
    # a todas las fechas que son mayores a 2022, le resto 100 años
    k = len(df.axes[0])
    for i in range(k):
        if df['fecha_nacimiento'][i].strftime('%Y') >= '2022':
            df['fecha_nacimiento'][i] = df['fecha_nacimiento'][i] - relativedelta(years=100)

    # elimino las filas con fecha de nacimiento mayor a la fecha de inscripcion y reseteo indices
    df = df.drop(df[df['inscription_date'] <= df['fecha_nacimiento']].index)
    df = df.reset_index(drop=True)

    # vuelvo a pasar el campo 'inscription_date' a string
    df['inscription_date'] = df['inscription_date'].astype(str)

    df['age'] = datetime.now() - df['fecha_nacimiento']
    df['age'] = (df['age'].dt.days)/365
    df['age'] = df['age'].astype(int)

    # elimino los datos con edades menores a 18
    df = df.drop(df[df['age'] < 18].index)
    df = df.reset_index(drop=True)
    # elimino la columna fecha de nacimiento
    df = df.drop(['fecha_nacimiento'], axis=1)
    
    df['location'] = df['location'].astype(str)
    df['location'] = df['location'].str.lower().str.replace("-", " ").str.replace("  ", " ")

    df_cp['localidad'] = df_cp['localidad'].astype(str)
    df_cp['localidad'] = df_cp['localidad'].str.lower().str.replace("-", " ").str.replace("  ", " ")

    # como existen localidades duplicadas con diferente CP, elimino las duplicadas 
    # y me quedo con la ultima suponiendo que es el registro mas nuevo
    df_cp = df_cp.drop_duplicates(['localidad'], keep='last')

    # inner join para obtener el codigo posatal partir de la localidad
    inner_df = pd.merge(left=df,right=df_cp, left_on='location', right_on='localidad')

    # elimino las columnas 'location' y 'codigo_postal'
    inner_df = inner_df.drop(['postal_code', 'localidad'], axis=1)
    #renombro columna codigo postal
    inner_df = inner_df.rename(columns = {'codigo_postal': 'postal_code'})
    df = inner_df
    #paso el CP a string
    df['postal_code'] = df['postal_code'].astype(str)

    # paso email a string (ya está pero por las dudas)
    # lo llevo todo a minusculas, elimino espacios extras y elimino guiones
    df['email'] = df['email'].astype(str)
    df['email'] = df['email'].str.lower().str.replace("-", " ").str.replace(" ", "")

    # Pasaje de dataframe a archivo de texto
    df.to_csv('/usr/local/airflow/tests/GE_Interamericana_process.txt', sep='\t', index=False)


# se define el DAG
with DAG(
    dag_id = "DAG_Uni_Interamericana_ETL",
    default_args = default_args,
    schedule_interval="@hourly",
    tags = ['ETL Universidad Interamericana (extract&transform&load)']
) as dag:

# se definen los tasks
    tarea_1 = PythonOperator(
        task_id = "extract",
        python_callable = extract_csv   # para ejecutar una función se llama con python_callable
        #op_args = [path_sql, path_csv]
    
    )

    tarea_2 = PythonOperator(
        task_id = "transform",
        python_callable = transform_pandas
    )

    tarea_3 = LocalFilesystemToS3Operator(
        task_id = "load",
        filename='/usr/local/airflow/tests/GE_Interamericana_process.txt',
        dest_key='GE_LaPampa_process.txt',
        dest_bucket='dipa-s3',
        aws_conn_id="aws_s3_bucket",
        replace=True
    )

# se definen las dependencias de las tareas
    tarea_1 >> tarea_2 >> tarea_3