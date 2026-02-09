-- Staging model for legacy invoice data from the old SAP-based ERP system.
-- The source table uses 8-character column names without underscores,
-- as was standard in SAP R/3 and similar legacy ERP systems.
--
-- Column mapping from legacy to human-readable names:
--   INVNO   → invoice_id       (unique invoice number)
--   CSTCODE → customer_code    (customer identifier, maps to customers via lookup)
--   AMTTTL  → total_amount     (total in cents, needs /100 for dollars)
--   TAXAMT  → tax_amount       (tax in cents, needs /100 for dollars)
--   DISCPCT → discount_percent (whole-number discount percentage, 0-100)
--   DTCREAT → created_at       (invoice creation date, ISO format)
--   STSCODE → status_code      (C=completed, P=pending, X=cancelled)
--   LOCCODE → location_code    (store/warehouse location identifier)

with

source as (

    select * from {{ source('legacy', 'raw_legacy_invoices') }}

),

renamed as (

    select

        ---------- ids
        INVNO as invoice_id,
        CSTCODE as customer_code,
        LOCCODE as location_code,

        ---------- amounts (convert cents to dollars)
        AMTTTL as total_amount_cents,
        TAXAMT as tax_amount_cents,
        round(AMTTTL / 100.0, 2) as total_amount,
        round(TAXAMT / 100.0, 2) as tax_amount,
        round((AMTTTL - TAXAMT) / 100.0, 2) as subtotal,

        ---------- discount
        DISCPCT as discount_percent,

        ---------- status
        -- Legacy status codes: C=completed, P=pending, X=cancelled
        STSCODE as status_code_raw,
        case STSCODE
            when 'C' then 'completed'
            when 'P' then 'pending'
            when 'X' then 'cancelled'
        end as status,

        ---------- timestamps
        cast(DTCREAT as date) as created_at

    from source

)

select * from renamed
