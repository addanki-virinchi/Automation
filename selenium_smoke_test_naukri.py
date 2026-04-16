from selenium import webdriver
from selenium.webdriver.chrome.service import Service

driver = webdriver.Chrome(service=Service())
driver.get("https://www.naukri.com/python-jobs-3?k=python&qproductJobSource=2&naukriCampus=true&experience=0&nignbevent_src=jobsearchDeskGNB")