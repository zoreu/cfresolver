from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import uvicorn
import logging
import asyncio
from contextlib import asynccontextmanager
import json
import requests
import subprocess

# Configuração de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Verificar versões de Chrome e ChromeDriver no início
try:
    chrome_version = subprocess.check_output(["google-chrome", "--version"]).decode().strip()
    chromedriver_version = subprocess.check_output(["chromedriver", "--version"]).decode().strip()
    logger.info(f"Google Chrome version: {chrome_version}")
    logger.info(f"ChromeDriver version: {chromedriver_version}")
except Exception as e:
    logger.error(f"Failed to check versions: {str(e)}")

# Modelo para a requisição
class RequestData(BaseModel):
    url: str
    params: dict = {}
    headers: dict = {}
    data: dict = {}  # Dados de formulário
    json_data: dict = {}  # Dados JSON
    form_selector: str = None
    submit_selector: str = None

# Gerenciador global para o WebDriver
class WebDriverManager:
    def __init__(self):
        self.driver = None

    def start_driver(self):
        if self.driver is None:
            logger.info("Initializing Selenium WebDriver...")
            chrome_options = Options()
            chrome_options.add_argument("--headless=new")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--disable-extensions")
            chrome_options.add_argument("--disable-infobars")
            chrome_options.add_argument("--disable-notifications")
            chrome_options.add_argument("--remote-debugging-port=9222")
            chrome_options.add_argument("--verbose")
            chrome_options.add_argument("--log-path=/app/chrome.log")
            service = Service(
                executable_path="/usr/local/bin/chromedriver",
                log_path="/app/chromedriver.log"
            )
            try:
                self.driver = webdriver.Chrome(service=service, options=chrome_options)
                logger.info("WebDriver initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize WebDriver: {str(e)}")
                raise
        return self.driver

    def stop_driver(self):
        if self.driver is not None:
            logger.info("Shutting down Selenium WebDriver...")
            try:
                self.driver.quit()
            except Exception as e:
                logger.warning(f"Failed to quit driver: {str(e)}")
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

# Gerenciamento do ciclo de vida do FastAPI
@asynccontextmanager
async def lifespan(app: FastAPI):
    driver_manager.start_driver()
    yield
    driver_manager.stop_driver()

app = FastAPI(title="Selenium Proxy API", lifespan=lifespan)

async def fetch_page(request_data: RequestData):
    driver = driver_manager.start_driver()
    try:
        logger.info(f"Fetching URL: {request_data.url} with params: {request_data.params}, headers: {request_data.headers}")
        url = request_data.url
        if request_data.params:
            query_string = "&".join([f"{key}={value}" for key, value in request_data.params.items()])
            url = f"{url}?{query_string}" if "?" not in url else f"{url}&{query_string}"

        if request_data.data and request_data.form_selector and request_data.submit_selector:
            driver.get(url)
            driver.implicitly_wait(10)
            form = driver.find_element(By.CSS_SELECTOR, request_data.form_selector)
            for key, value in request_data.data.items():
                input_field = form.find_element(By.NAME, key)
                input_field.clear()
                input_field.send_keys(value)
            submit_button = driver.find_element(By.CSS_SELECTOR, request_data.submit_selector)
            submit_button.click()
            driver.implicitly_wait(10)
            page_content = driver.page_source
        elif request_data.json_data:
            try:
                response = requests.post(
                    url,
                    json=request_data.json_data,
                    headers=request_data.headers,
                    params=request_data.params
                )
                response.raise_for_status()
                driver.get(url)
                driver.implicitly_wait(10)
                page_content = driver.page_source
            except requests.RequestException as e:
                logger.error(f"Error in POST request: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error in POST request: {str(e)}")
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
    request_data = RequestData(url=url, params=params or {}, headers=headers or {})
    result = await fetch_page(request_data)
    return result

# Endpoint POST
@app.post("/proxy")
async def proxy_post(request_data: RequestData):
    result = await fetch_page(request_data)
    return result

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860)
