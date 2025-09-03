import streamlit as st
import pandas as pd
import requests
import json

# --- BIBLIOTECAS NECESSÁRIAS ---
try:
    import google.generativeai as genai
    from GoogleNews import GoogleNews
    from pydantic import BaseModel, Field
    from typing import List
except ImportError as e:
    st.error(f"""
        Uma ou mais bibliotecas necessárias não foram encontradas.
        Por favor, instale a biblioteca de busca de notícias executando:

        pip install GoogleNews

        E as outras dependências, se necessário:
        pip install streamlit pandas requests google-generativeai pydantic

        Erro original: {e}
    """)
    st.stop()

# --- CHAVES DE API ---
try:
    JINA_API_KEY = st.secrets["JINA_API_KEY"]
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=GEMINI_API_KEY)
except (KeyError, FileNotFoundError):
    st.error("Erro: Chaves JINA_API_KEY ou GEMINI_API_KEY não encontradas. Verifique seu arquivo .streamlit/secrets.toml.")
    st.stop()


# --- SUA FUNÇÃO DE BUSCA RESTAURADA ---
def buscar_google_news(termo):
    # Inicializa o objeto GoogleNews
    googlenews = GoogleNews(lang='pt-BR', period='7d', encode='utf-8')

    # Realiza a busca com o termo do usuário
    googlenews.search(termo)

    # --- SEU CÓDIGO DE PAGINAÇÃO REINSERIDO AQUI ---
    # Define o número máximo de resultados desejados
    max_resultados = 2000
    resultados = []
    pagina = 1

    # Itera sobre as páginas de resultados até atingir o número desejado
    # Adicionado um status para o usuário ver o progresso da busca longa
    status_text = st.empty()
    while len(resultados) < max_resultados:
        status_text.text(f"Buscando notícias... Página {pagina}, {len(resultados)} resultados encontrados.")
        googlenews.get_page(pagina)
        noticias_pagina = googlenews.result(sort=True) # Usar sort=True pode ajudar na ordem
        if not noticias_pagina:
            break  # Encerra se não houver mais resultados
        resultados.extend(noticias_pagina)
        pagina += 1
    status_text.empty()

    # Limita a lista de resultados ao número máximo desejado
    resultados = resultados[:max_resultados]
    
    if not resultados:
        return pd.DataFrame()

    # Exibe os resultados no console
    quantidade_noticias = len(resultados)
    print(f'Quantidade de notícias retornadas: {quantidade_noticias}')

    # Coloca todas as noticias num dataframe
    df = pd.DataFrame(resultados)
    
    # Limpeza e formatação do DataFrame
    df['link'] = df['link'].str.split('&ved').str[0]
    df.rename(columns={'media': 'source'}, inplace=True)

    # Garante que as colunas essenciais existam antes de retornar
    colunas_necessarias = {'title', 'link', 'source'}
    if not colunas_necessarias.issubset(df.columns):
        st.warning("A busca não retornou as colunas esperadas (title, link, source).")
        return pd.DataFrame()

    return df[['title', 'link', 'source']]


# --- FUNÇÃO 'PEGA_NOTICIAS' (COM CACHE) ---
@st.cache_data(ttl=3600)
def pega_noticias(termo_busca):
    """Busca notícias, combina e remove duplicatas."""
    # O spinner agora envolve a chamada da função que pode ser longa
    with st.spinner("Realizando busca aprofundada de notícias... Isso pode levar alguns minutos."):
        todas_as_noticias = buscar_google_news(termo_busca)

    if todas_as_noticias.empty:
        return pd.DataFrame()

    # Limpa e remove duplicatas
    todas_as_noticias.dropna(subset=['link'], inplace=True)
    noticias_unicas = todas_as_noticias.drop_duplicates(subset=['link'], keep='first')
    noticias_unicas = noticias_unicas.drop_duplicates(subset=['title'], keep='first')
    noticias_unicas.reset_index(drop=True, inplace=True)
    
    st.success(f"Busca concluída! {noticias_unicas.shape[0]} notícias únicas encontradas.")
    return noticias_unicas


# --- FUNÇÃO DE EXTRAÇÃO DE CONTEÚDO (INTOCADA) ---
@st.cache_data(ttl=3600)
def extrair_conteudo_noticias(df_noticias):
    # (O código desta função permanece o mesmo)
    conteudos = []
    headers = {"Authorization": f"Bearer {JINA_API_KEY}", "X-Engine": "browser"}
    total_noticias = len(df_noticias)
    progress_bar = st.progress(0)
    status_text = st.empty()
    for i, (index, row) in enumerate(df_noticias.iterrows()):
        status_text.text(f"Extraindo notícia {i + 1}/{total_noticias}: {row['title'][:50]}...")
        url = f"https://r.jina.ai/{row['link']}"
        try:
            response = requests.get(url, headers=headers, timeout=20)
            response.raise_for_status()
            conteudos.append(response.text)
        except requests.exceptions.RequestException as e:
            conteudos.append(f"Erro ao buscar conteúdo para o título '{row['title']}': {e}")
        progress_bar.progress((i + 1) / total_noticias)
    status_text.empty()
    return pd.DataFrame({'title': df_noticias['title'], 'link': df_noticias['link'], 'content': conteudos})

# --- FUNÇÃO GEMINI (INTOCADA) ---
@st.cache_data(ttl=3600)
def processa_noticias_com_gemini(df_conteudos):
    # (O código desta função permanece o mesmo)
    respostas_json = []
    links_originais = df_conteudos['link'].tolist()
    for i, texto in enumerate(df_conteudos['content']):
        if texto.startswith("Erro ao buscar conteúdo"):
            respostas_json.append(json.dumps({"titulo": "Conteúdo da notícia não disponível"}))
            continue
        try:
            model = genai.GenerativeModel(model_name="gemini-1.5-pro-latest")
            response = model.generate_content(
                f"""
                Analise o seguinte texto de uma notícia e extraia as informações no formato JSON.
                O JSON deve seguir a seguinte estrutura:
                {{
                    "titulo": "O título da notícia.",
                    "data_de_publicacao": "A data em que a notícia foi publicada (se disponível).",
                    "resumo_curto": "Um resumo conciso da notícia, apensa com o assunto principal da noticia, não precisa enrolar muito, apenas o basico para um usuario entender do que se trata a noticia, entre 30 palavras e 50 palavras.",
                    "resumo_maior": "Um resumo mais detalhado da notícia, apenas com as informações mais relevantes da noticia e algumas observações a mais, com mais de 150 palavras.",
                    "links_de_imagens": ["Uma lista contendo até 2 URLs das imagens mais relevantes da notícia. Se não houver, retorne uma lista vazia."]
                }}
                Texto da notícia:
                ---
                {texto}
                """,
                generation_config={"response_mime_type": "application/json"}
            )
            noticia_processada = json.loads(response.text)
            noticia_processada['link'] = links_originais[i]
            respostas_json.append(json.dumps(noticia_processada, ensure_ascii=False))
        except Exception as e:
            print(f"Erro ao processar notícia com Gemini: {e}")
            respostas_json.append(json.dumps({"titulo": "Conteúdo da notícia não disponível"}))
    return respostas_json

# --- FUNÇÃO DE RENDERIZAÇÃO (INTOCADA) ---
def gerar_newsletter_streamlit(lista_json):
    # (O código desta função permanece o mesmo)
    if not lista_json:
        st.info("Nenhuma notícia processada para exibir.")
        return
    noticias_exibidas = 0
    for i, noticia_str in enumerate(lista_json):
        try:
            noticia = json.loads(noticia_str)
            if not noticia or noticia.get("titulo") == "Conteúdo da notícia não disponível":
                continue
        except (json.JSONDecodeError, AttributeError):
            continue
        titulo = noticia.get("titulo", "Título não encontrado")
        data = noticia.get("data_de_publicacao", "Data não informada")
        resumo_curto = noticia.get("resumo_curto", "")
        resumo_maior = noticia.get("resumo_maior", "")
        link = noticia.get("link", "#")
        imagens = noticia.get("links_de_imagens", [])
        imagem = imagens[0] if imagens else "https://via.placeholder.com/400x267?text=Sem+Imagem"
        with st.container(border=True):
            col_img, col_content = st.columns([1, 3])
            with col_img:
                st.image(imagem)
            with col_content:
                st.subheader(titulo)
                st.caption(f"Publicado em: {data}")
                st.write(resumo_curto)
                if resumo_maior:
                    with st.expander("Ler resumo completo..."):
                        st.write(resumo_maior)
                st.markdown(f'<a href="{link}" target="_blank" style="color: #0a9396; font-weight: bold;">Ler notícia completa ↗</a>', unsafe_allow_html=True)
        noticias_exibidas += 1
    st.write(f"**Exibindo {noticias_exibidas} notícias processadas.**")

# --- INTERFACE PRINCIPAL DO STREAMLIT (INTOCADA) ---
st.set_page_config(page_title="Gerador de Newsletter com IA", layout="centered")
st.title("📰 Gerador de Newsletter com IA")
st.markdown("Digite um tema, clique em gerar e obtenha um resumo das últimas notícias do Google News, processado por Inteligência Artificial.")

termo_busca = st.text_input("Qual tema você quer pesquisar?", placeholder="Ex: Novidades sobre o clima")

if st.button("Gerar Newsletter", type="primary"):
    if not termo_busca:
        st.warning("Por favor, digite um termo para a busca.")
    else:
        df_noticias = pega_noticias(termo_busca)
        if not df_noticias.empty:
            df_conteudos = extrair_conteudo_noticias(df_noticias)
            with st.spinner("A Inteligência Artificial está analisando e resumindo as notícias..."):
                resumos_json = processa_noticias_com_gemini(df_conteudos)
            st.success("Newsletter gerada com sucesso!")
            st.markdown("---")
            gerar_newsletter_streamlit(resumos_json)
        else:
            st.error(f"Nenhuma notícia encontrada no Google News para o termo '{termo_busca}'. Tente outra palavra-chave.")
