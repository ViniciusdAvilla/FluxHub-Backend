from app.database.repositories.products import ProductRepository


class ProductService:

    def __init__(
        self,
        repository: ProductRepository,
    ):
        self.repository = repository


    def get_products(self):

        return self.repository.get_all()


    def count_products(self):

        return self.repository.count_products()


    def get_top_discount(self):

        return self.repository.get_top_discount()


    def get_top_stock(self):

        return self.repository.get_top_stock()

    def get_catalog_health(self):

         return self.repository.get_catalog_health()

    def get_categories(self):

        return self.repository.get_categories()

    def get_brands(self):

        return self.repository.get_brands()

    def get_out_of_stock(self):

        return self.repository.get_out_of_stock()

    def get_low_stock(self):

        return self.repository.get_low_stock()
    
    def get_low_rating(self):

     return self.repository.get_low_rating()

    def get_top_rated(self):

        return self.repository.get_top_rated()