from fastapi import FastAPI

from app.routers import app_settings, auth, customers, driver_messages, drivers, health, orders, products, routes, traccar, users

app = FastAPI(title="Auto Dispatch")
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(drivers.router)
app.include_router(driver_messages.router)
app.include_router(products.router)
app.include_router(customers.router)
app.include_router(orders.router)
app.include_router(traccar.router)
app.include_router(routes.router)
app.include_router(app_settings.router)
