from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import uvicorn
import logging
import asyncio
from contextlib import asynccontextmanager
import json
import requests

# Configuração de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Gerenciador do ciclo de vida do FastAPI
@asynccontextmanager
async def lifespan(app: FastAPI):
    driver_manager.start_driver()
    yield
    driver_manager.stop_driver()

app = FastAPI(title="Selenium Proxy API", lifespan=lifespan)

# Modelo para a requisição POST
class RequestData(BaseModel):
    url: str
    params: dict = {}
    headers: dict = {}
    data: dict = {}  # Dados de formulário (application/x-www-form-urlencoded)
    json: dict = {}  # Dados JSON (application/json)
    form_selector: str = None  # Seletor CSS do formulário (opcional)
    submit_selector: str = None  # Seletor CSS do botão de submit (opcional)

# Gerenciador global para o WebDriver
class WebDriverManager:
    def __init__(self):
        self.driver = None

    def start_driver(self):
        if self.driver is None:
            logger.info("Initializing Selenium WebDriver...")
            chrome_options = Options()
            chrome_options.add_argument("--headless")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            self.driver = webdriver.Chrome(
                service=Service(ChromeDriverManager().install()), options=chrome_options
            )
        return self.driver

    def stop_driver(self):
        if self.driver is not None:
            logger.info("Shutting down Selenium WebDriver...")
            self.driver.quit()
            self.driver = None

    async def reset_driver(self):
        try:
            self.driver.delete_all_cookies()
            self.driver.execute_script("window.localStorage.clear();")
            self.driver.execute_script("window.sessionStorage.clear();")
        except Exception as e:
            logger.warning(f"Failed to reset driver state: {str(e)}")
            self.stop_driver()
            self.start_driver()

driver_manager = WebDriverManager()

# Função para processar a requisição com Selenium
async def fetch_page(request_data: RequestData):
    driver = driver_manager.start_driver()
    try:
        logger.info(f"Fetching URL: {request_data.url} with params: {request_data.params}, headers: {request_data.headers}")

        # Monta a URL com parâmetros, se fornecidos
        url = request_data.url
        if request_data.params:
            query_string = "&".join([f"{key}={value}" for key, value in request_data.params.items()])
            url = f"{url}?{query_string}" if "?" not in url else f"{url}&{query_string}"

        # Se houver dados de formulário e seletores de formulário/submit, preenche o formulário
        if request_data.data and request_data.form_selector and request_data.submit_selector:
            driver.get(url)
            driver.implicitly_wait(10)

            # Preenche o formulário
            form = driver.find_element(By.CSS_SELECTOR, request_data.form_selector)
            for key, value in request_data.data.items():
                input_field = form.find_element(By.NAME, key)
                input_field.clear()
                input_field.send_keys(value)

            # Envia o formulário
            submit_button = driver.find_element(By.CSS_SELECTOR, request_data.submit_selector)
            submit_button.click()

            # Aguarda o carregamento da página resultante
            driver.implicitly_wait(10)
            page_content = driver.page_source

        # Se houver dados JSON, tenta enviar via JavaScript ou faz um POST com requests
        elif request_data.json:
            # Abordagem híbrida: faz o POST com requests e carrega a página resultante com Selenium
            try:
                response = requests.post(
                    url,
                    json=request_data.json,
                    headers=request_data.headers,
                    params=request_data.params
                )
                response.raise_for_status()
                # Carrega a URL resultante ou a mesma URL para capturar o estado pós-POST
                driver.get(url)
                driver.implicitly_wait(10)
                page_content = driver.page_source
            except requests.RequestException as e:
                logger.error(f"Error in POST request: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error in POST request: {str(e)}")

        # Caso contrário, faz um GET simples
        else:
            driver.get(url)
            driver.implicitly_wait(10)
            page_content = driver.page_source

        await driver_manager.reset_driver()
        return {"status": "success", "content": page_content}

    except Exception as e:
        logger.error(f"Error fetching URL {url}: {str(e)}")
        driver_manager.stop_driver()
        driver_manager.start_driver()
        raise HTTPException(status_code=500, detail=f"Error fetching URL: {str(e)}")

# Endpoint GET
@app.get("/proxy")
async def proxy_get(url: str, params: dict = None, headers: dict = None):
    """
    Faz uma requisição GET para a URL fornecida usando Selenium.
    Exemplo: /proxy?url=https://example.com¶ms={"key":"value"}&headers={"User-Agent":"Mozilla/5.0"}
    """
    request_data = RequestData(url=url, params=params or {}, headers=headers or {})
    result = await fetch_page(request_data)
    return result

# Endpoint POST
@app.post("/proxy")
async def proxy_post(request_data: RequestData):
    """
    Faz uma requisição POST para a URL fornecida usando Selenium.
    Exemplo de corpo da requisição:
    {
        "url": "https://example.com",
        "params": {"key": "value"},
        "headers": {"User-Agent": "Mozilla/5.0"},
        "data": {"field1": "value1"},  // Para formulários
        "json": {"key": "value"},      // Para APIs JSON
        "form_selector": "form#myForm",
        "submit_selector": "button[type=submit]"
    }
    """
    result = await fetch_page(request_data)
    return result

# # Iniciar o servidor
# if __name__ == "__main__":
#     uvicorn.run(app, host="0.0.0.0", port=8000)