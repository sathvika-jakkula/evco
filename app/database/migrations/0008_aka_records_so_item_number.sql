-- soItemNumber: the quote PDF's "Mold" column, exposed under the AKA API
-- contract as soItemNumber (the name used on the customer/IQMS side), not
-- previously carried anywhere in get-aka/create-aka/update-aka.
ALTER TABLE aka_records ADD COLUMN IF NOT EXISTS so_item_number TEXT;
